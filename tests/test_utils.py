"""Unit tests for agents/utils.py shared functions."""
from unittest.mock import patch

FAKE_AGENT_PROFILES = {
    "quant_researcher": {
        "emoji": "",
        "name_cn": "量化研究员",
        "color": "#22c55e",
    },
}


# ── test_build_agent_log ──────────────────────────────────────────────


@patch("backend.agents.utils.AGENT_PROFILES", FAKE_AGENT_PROFILES)
def test_build_agent_log_structure():
    """verify the standard dict returned by build_agent_log."""
    from backend.agents.utils import build_agent_log

    result = build_agent_log("quant_researcher", "分析完毕")

    assert isinstance(result, dict)
    assert result["agent"] == "quant_researcher"
    assert result["emoji"] == ""
    assert result["name_cn"] == "量化研究员"
    assert result["color"] == "#22c55e"
    assert result["content"] == "分析完毕"
    assert "timestamp" in result
    assert result.get("is_chat_mode") is None


@patch("backend.agents.utils.AGENT_PROFILES", FAKE_AGENT_PROFILES)
def test_build_agent_log_unknown_agent_falls_back():
    from backend.agents.utils import build_agent_log

    result = build_agent_log("non_existent_agent", "test content")

    assert result["emoji"] == ""
    assert result["name_cn"] == "non_existent_agent"
    assert result["color"] == "#6366f1"


@patch("backend.agents.utils.AGENT_PROFILES", FAKE_AGENT_PROFILES)
def test_build_agent_log_chat_mode():
    from backend.agents.utils import build_agent_log

    msgs = [{"role": "user", "content": "hello"}]
    result = build_agent_log(
        "quant_researcher", "summary", is_chat_mode=True, chat_messages=msgs
    )

    assert result["is_chat_mode"] is True
    assert result["chat_messages"] == msgs


# ── test_append_agent_log ────────────────────────────────────────────


def test_append_agent_log_appends_to_list():
    from backend.agents.utils import append_agent_log

    state = {"agent_logs": []}
    entry = {"agent": "test", "content": "hello"}

    append_agent_log(state, entry)

    assert len(state["agent_logs"]) == 1
    assert state["agent_logs"][0] is entry


def test_append_agent_log_creates_key_if_missing():
    from backend.agents.utils import append_agent_log

    state = {}
    entry = {"agent": "test", "content": "hello"}

    append_agent_log(state, entry)

    assert "agent_logs" in state
    assert state["agent_logs"] == [entry]


# ── test_join_lines ──────────────────────────────────────────────────


def test_join_lines_with_body():
    from backend.agents.utils import join_lines

    result = join_lines("标题：", "● 第一行", "● 第二行")
    assert result == "标题：\n\n● 第一行\n● 第二行"


def test_join_lines_title_only():
    from backend.agents.utils import join_lines

    result = join_lines("只有标题")
    assert result == "只有标题\n"


# ── test_safe_int ───────────────────────────────────────────────────


def test_safe_int_from_int():
    from backend.agents.utils import safe_int

    assert safe_int(100) == 100
    assert safe_int(0) == 0


def test_safe_int_from_float():
    from backend.agents.utils import safe_int

    assert safe_int(100.7) == 100
    assert safe_int(0.5) == 0


def test_safe_int_from_numeric_string():
    from backend.agents.utils import safe_int

    # LLM 常把数量以字符串返回，这是历史 TypeError 的根因
    assert safe_int("100") == 100
    assert safe_int("100.0") == 100
    assert safe_int(" 200 ") == 200


def test_safe_int_invalid_returns_none():
    from backend.agents.utils import safe_int

    assert safe_int(None) is None
    assert safe_int("abc") is None
    assert safe_int("") is None
    assert safe_int(True) is None
    assert safe_int([100]) is None


def test_safe_int_prevents_str_int_comparison():
    from backend.agents.utils import safe_int

    # 复现线上报错场景：LLM 返回字符串数量，min() 与 int 比较
    raw_quantity = "100"
    max_sellable = 500
    quantity = safe_int(raw_quantity) or max_sellable
    suggested = min(quantity, max_sellable)
    assert suggested == 100


# ── test_maybe_attach_followup ───────────────────────────────────────


def test_maybe_attach_followup_attaches_on_probability_hit():
    from backend.agents.utils import AGENT_FOLLOWUP_POOLS, maybe_attach_followup

    log = {"agent": "quant_researcher", "content": "分析完毕"}
    result = maybe_attach_followup(log, "quant_researcher", probability=1.0)

    assert "followups" in result
    assert len(result["followups"]) == 1
    assert result["followups"][0] in AGENT_FOLLOWUP_POOLS["quant_researcher"]
    # 不修改原 dict
    assert "followups" not in log


def test_maybe_attach_followup_skips_on_probability_miss():
    from backend.agents.utils import maybe_attach_followup

    log = {"agent": "quant_researcher", "content": "分析完毕"}
    result = maybe_attach_followup(log, "quant_researcher", probability=0.0)

    assert "followups" not in result


def test_maybe_attach_followup_skips_chat_mode():
    from backend.agents.utils import maybe_attach_followup

    log = {"agent": "quant_researcher", "content": "x", "is_chat_mode": True}
    result = maybe_attach_followup(log, "quant_researcher", probability=1.0)

    assert "followups" not in result


# ── test_explicit_report_request（报告确定性守卫） ───────────────────


def test_report_guard_blocks_light_view():
    from backend.agents.chief_strategist import _explicit_report_request

    assert _explicit_report_request("看看持仓") is False
    assert _explicit_report_request("看看茅台") is False
    assert _explicit_report_request("持仓怎么样") is False
    assert _explicit_report_request("大盘如何") is False


def test_report_guard_allows_explicit_report():
    from backend.agents.chief_strategist import _explicit_report_request

    assert _explicit_report_request("写个持仓分析报告") is True
    assert _explicit_report_request("帮我深度分析五洲新春") is True
    assert _explicit_report_request("全面评估我的持仓风险") is True


# ── test_is_direct_execute（直接执行确定性守卫） ─────────────────────


def test_direct_execute_guard_detects_keywords():
    from backend.agents.chief_strategist import _is_direct_execute

    assert _is_direct_execute("直接买入茅台") is True
    assert _is_direct_execute("立即卖出贵州燃气") is True
    assert _is_direct_execute("马上挂单买入") is True
    assert _is_direct_execute("立刻下单") is True


def test_direct_execute_guard_skips_plain_trade():
    from backend.agents.chief_strategist import _is_direct_execute

    assert _is_direct_execute("买入茅台") is False
    assert _is_direct_execute("卖出贵州燃气") is False
    assert _is_direct_execute("") is False
    assert _is_direct_execute(None) is False
