"""Unit tests for Headline Arena 每日预测任务的纯函数（不测网络）"""

import pytest

from backend.services.arena_daily import (
    DEAD_ZONE_PCT,
    _parse_llm_json,
    build_prediction_messages,
    normalize_probabilities,
    sanitize_context,
)

FAKE_CONTEXT = {
    "asset": "PALM",
    "price": 9589.0,
    "change_1h": 0.3979,
    "change_4h": 0.3663,
    "indicators": {"rsi14": 59.09, "ema20": 9568.01, "ema50": 9568.60, "vol_20": 0.12},
    "baseline": {"bullish": 0.42, "bearish": 0.33, "neutral": 0.25},
}


# ── build_prediction_messages ─────────────────────────────────────────


def test_build_prediction_messages_structure():
    """消息包含题目/结算规则/市场上下文，且 system 要求严格 JSON 输出。"""
    msgs = build_prediction_messages("棕榈油今日涨跌？", "以结算价相对开盘价判断", FAKE_CONTEXT)

    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"

    system = msgs[0]["content"]
    # system 提示词要求三键概率 JSON 输出
    for key in ("bearish", "neutral", "bullish"):
        assert key in system
    assert "JSON" in system
    # 死区说明
    assert "0.3" in system

    user = msgs[1]["content"]
    assert "棕榈油今日涨跌？" in user
    assert "以结算价相对开盘价判断" in user
    # 上下文 JSON 必须可反解且包含关键数字
    assert "9589.0" in user
    assert "59.09" in user


def test_build_prediction_messages_empty_criteria():
    """结算规则缺失时仍可组装，用通用规则占位。"""
    msgs = build_prediction_messages("题目", "", FAKE_CONTEXT)
    assert "题目" in msgs[1]["content"]
    assert "通用规则" in msgs[1]["content"]


# ── normalize_probabilities ───────────────────────────────────────────


def test_normalize_probabilities_passthrough():
    """和恰为 1 的输入保持不变（浮点容差内）。"""
    probs = normalize_probabilities({"bearish": 0.55, "neutral": 0.30, "bullish": 0.15})
    assert probs == {"bearish": 0.55, "neutral": 0.30, "bullish": 0.15}
    assert abs(sum(probs.values()) - 1.0) < 1e-9


def test_normalize_probabilities_rescales():
    """和不为 1 时归一化到和为 1。"""
    probs = normalize_probabilities({"bearish": 1, "neutral": 1, "bullish": 1})
    assert probs == {"bearish": 1 / 3, "neutral": 1 / 3, "bullish": 1 / 3}
    assert abs(sum(probs.values()) - 1.0) < 1e-9


def test_normalize_probabilities_clamps():
    """越界值 clamp 到 [0,1] 后再归一化（先 clamp 后归一化，非截断到 1）。"""
    probs = normalize_probabilities({"bearish": 1.5, "neutral": -0.2, "bullish": 0.5})
    # clamp 后 (1.0, 0.0, 0.5)，归一化得 (2/3, 0, 1/3)
    assert probs["bearish"] == pytest.approx(2 / 3)
    assert probs["neutral"] == 0.0
    assert probs["bullish"] == pytest.approx(1 / 3)
    assert abs(sum(probs.values()) - 1.0) < 1e-9


@pytest.mark.parametrize(
    "raw",
    [
        {"bearish": 0.5, "neutral": 0.5},  # 缺 bullish
        {},  # 全缺
        ["bearish", "neutral", "bullish"],  # 非 dict
    ],
)
def test_normalize_probabilities_invalid_inputs(raw):
    with pytest.raises(ValueError):
        normalize_probabilities(raw)


def test_normalize_probabilities_extra_keys_ok():
    """多余键被忽略，三键齐全即可归一化。"""
    probs = normalize_probabilities(
        {"bearish": 0.2, "neutral": 0.3, "bullish": 0.5, "summary": "x"}
    )
    assert abs(sum(probs.values()) - 1.0) < 1e-9
    assert "summary" not in probs


def test_normalize_probabilities_non_numeric():
    with pytest.raises(ValueError):
        normalize_probabilities({"bearish": "0.5", "neutral": 0.25, "bullish": 0.25})


def test_normalize_probabilities_all_zero():
    with pytest.raises(ValueError):
        normalize_probabilities({"bearish": 0, "neutral": 0, "bullish": 0})


# ── _parse_llm_json ───────────────────────────────────────────────────


def test_parse_llm_json_with_fences():
    """容忍 ```json 围栏。"""
    raw = '```json\n{"bearish": 0.2, "neutral": 0.3, "bullish": 0.5}\n```'
    data = _parse_llm_json(raw)
    assert data["bullish"] == 0.5


def test_parse_llm_json_with_noise():
    """容忍前后杂文本，取首个 { 到末个 }。"""
    raw = '好的，我的判断如下：{"bearish": 0.2, "neutral": 0.3, "bullish": 0.5} 请查收'
    data = _parse_llm_json(raw)
    assert isinstance(data, dict)
    assert data["bearish"] == 0.2


# ── sanitize_context（实测平台 recent_events 混入远期日历事件） ─────────


def test_sanitize_context_drops_far_future_events():
    ctx = {
        "as_of": "2026-09-29T16:00:00Z",
        "recent_events": [
            {"title": "今日美盘库存", "timestamp": "2026-09-30T14:30:00"},
            {"title": "一个月后的 ECB", "timestamp": "2026-10-29T13:45:00"},
        ],
    }
    out = sanitize_context(ctx)
    titles = [e["title"] for e in out["recent_events"]]
    assert "今日美盘库存" in titles
    assert "一个月后的 ECB" not in titles
    # 不修改原对象
    assert len(ctx["recent_events"]) == 2


def test_sanitize_context_keeps_unparseable_and_no_events():
    ctx = {"as_of": "2026-09-29T16:00:00Z", "recent_events": [{"title": "无时间戳"}]}
    assert sanitize_context(ctx)["recent_events"] == [{"title": "无时间戳"}]
    assert sanitize_context({"price": 1}) == {"price": 1}
    assert sanitize_context("not dict") == {}


def test_build_prediction_messages_filters_noise_events():
    ctx = {
        **FAKE_CONTEXT,
        "as_of": "2026-09-29T16:00:00Z",
        "recent_events": [{"title": "远期噪声事件 XYZ", "timestamp": "2026-12-01T00:00:00"}],
    }
    user_msg = build_prediction_messages("Q", "R", ctx)[1]["content"]
    assert "远期噪声事件 XYZ" not in user_msg
