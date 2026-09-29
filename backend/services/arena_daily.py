"""Headline Arena 每日预测任务

流程：巡检开放题目 → 找到配置资产的 daily 挑战 → 幂等检查（已提交则跳过）→
拉取市场上下文 → LLM 生成概率分布 → 归一化校验 → 提交到第三方结算。

结算由 Headline Arena 按真实行情机械执行（±0.3% 死区），与本地模拟盘相互独立。
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from .. import config
from .headline_arena_client import (
    _credentials,
    get_market_context,
    get_my_predictions,
    get_open_challenges,
    submit_prediction,
)
from .llm import choose_client
from .task_manager import task_manager

logger = logging.getLogger(__name__)

# 与 Arena 结算规则一致：涨跌幅落在 ±0.3% 死区内判 neutral
DEAD_ZONE_PCT = 0.3

# recent_events 实际是宏观日历（实测混入一个月后的事件），日度预测只保留近窗事件
EVENT_LOOKBACK_DAYS = 2
EVENT_LOOKAHEAD_DAYS = 3


def _parse_ts(value) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def sanitize_context(context: dict) -> dict:
    """过滤上下文中与日度方向无关的噪声（实测 recent_events 含远期日历事件）。"""
    if not isinstance(context, dict):
        return {}
    result = dict(context)
    events = context.get("recent_events")
    if isinstance(events, list):
        now = _parse_ts(context.get("as_of")) or datetime.now(timezone.utc)
        lo, hi = now - timedelta(days=EVENT_LOOKBACK_DAYS), now + timedelta(days=EVENT_LOOKAHEAD_DAYS)
        kept = []
        for e in events:
            if not isinstance(e, dict):
                continue
            ts = _parse_ts(e.get("timestamp"))
            if ts is None or lo <= ts <= hi:
                kept.append(e)
        result["recent_events"] = kept
    return result


_SYSTEM_PROMPT = (
    "你是一名严谨的期货市场方向预测员，负责对大宗商品/金融期货的日内涨跌方向给出概率分布判断。"
    "你必须输出严格的 JSON 对象（不要任何多余文本或 Markdown 围栏），格式为：\n"
    '{"bearish": 概率, "neutral": 概率, "bullish": 概率, "summary": "一句话结论", "reasoning": "推理链"}\n'
    "三个概率取值 0~1 且三者之和必须恰好等于 1；summary 用一两句中文概括结论；"
    "reasoning 用 3-6 句中文给出完整推理链，必须引用市场上下文中的具体数字"
    "（当前价相对开盘价的涨跌幅、RSI14、EMA20/EMA50 位置、基线人群分布等）。"
    "注意结算规则：结算价相对开盘价涨幅 > +0.3% 判 bullish，< -0.3% 判 bearish，"
    "其余（±0.3% 死区内）判 neutral——请据此给概率赋值，不要忽视死区的存在。"
)


def build_prediction_messages(
    question: str, criteria_zh: str, context: dict
) -> list[dict]:
    """组装预测用 LLM 消息（纯函数，便于单测）。"""
    ctx_json = json.dumps(sanitize_context(context), ensure_ascii=False, indent=2)
    user = (
        f"# 题目\n{question}\n\n"
        f"# 结算规则\n{criteria_zh or '（官方未提供细则，按通用规则：结算价相对开盘价判断方向）'}\n\n"
        f"# 死区说明\n结算价相对开盘价涨跌幅在 ±{DEAD_ZONE_PCT}% 之内判 neutral，"
        f"超过 +{DEAD_ZONE_PCT}% 判 bullish，低于 -{DEAD_ZONE_PCT}% 判 bearish。\n\n"
        f"# 市场上下文（实时行情与指标，price 为当前价，可与题目 open_price 对比）\n{ctx_json}\n\n"
        "请输出严格 JSON：{\"bearish\": x, \"neutral\": y, \"bullish\": z, \"summary\": \"...\", \"reasoning\": \"...\"}，"
        "三个概率之和为 1。"
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def normalize_probabilities(raw: dict) -> dict:
    """校验并归一化三向概率：clamp 到 [0,1] 后归一化使和为 1（纯函数）。

    非法输入（缺键/非数值/全为 0）抛 ValueError。
    """
    if not isinstance(raw, dict):
        raise ValueError(f"概率分布必须是 dict，收到 {type(raw).__name__}")
    keys = ("bearish", "neutral", "bullish")
    vals: dict[str, float] = {}
    for k in keys:
        if k not in raw:
            raise ValueError(f"概率分布缺少键 {k}")
        v = raw[k]
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise ValueError(f"概率 {k} 不是数值: {v!r}")
        vals[k] = min(max(float(v), 0.0), 1.0)

    total = sum(vals.values())
    if total <= 0:
        raise ValueError(f"概率之和必须大于 0，收到 {total}")
    return {k: vals[k] / total for k in keys}


def _parse_llm_json(raw: str) -> dict:
    """解析 LLM 输出 JSON，容忍 ```json 围栏与前后杂文本。"""
    text = (raw or "").strip()
    if text.startswith("```"):
        # 去掉 ```json ... ``` 围栏
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        text = text[start : end + 1]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("LLM 输出不是 JSON 对象")
    return data


async def run_arena_daily(trigger: str = "manual") -> dict:
    """对配置的每个资产提交（或跳过）今日预测，返回汇总结果。"""
    summary = {"trigger": trigger, "assets": [], "submitted": 0, "skipped": 0, "failed": 0}
    challenges = await get_open_challenges()
    if not challenges:
        logger.info("Arena 每日预测：无开放题目或获取失败，本轮跳过")
        return summary

    try:
        my_preds = await get_my_predictions()
    except Exception as e:
        logger.warning("Arena 每日预测：读取历史预测失败 %s", e)
        my_preds = []
    submitted_ids = {
        p.get("challenge_id") or (p.get("challenge") or {}).get("id")
        for p in my_preds
        if isinstance(p, dict)
    }

    for asset in config.HEADLINE_ARENA_DAILY_ASSETS:
        entry = {"asset": asset, "status": "failed", "reason": ""}
        try:
            # 找该资产的 open daily 挑战（asset 精确匹配，兼容大小写）
            challenge = next(
                (
                    c
                    for c in challenges
                    if str(c.get("asset", "")).upper() == asset
                    and c.get("challenge_type", "daily") == "daily"
                ),
                None,
            )
            if not challenge:
                entry.update(status="skipped", reason="无该资产的开放题目")
                summary["assets"].append(entry)
                summary["skipped"] += 1
                continue

            challenge_id = challenge.get("id", "")
            if challenge_id in submitted_ids:
                entry.update(status="skipped", reason="该题目已提交过（幂等跳过）")
                summary["assets"].append(entry)
                summary["skipped"] += 1
                continue

            context = await get_market_context(asset)
            if not context:
                entry["reason"] = "获取市场上下文失败"
                summary["assets"].append(entry)
                summary["failed"] += 1
                continue

            messages = build_prediction_messages(
                challenge.get("question", ""),
                challenge.get("resolution_criteria_zh", ""),
                context,
            )
            # 深度模型思考耗 token，max_tokens 需给足；失败时回退 flash 重试一次
            raw = ""
            data: dict | None = None
            for deep, max_tokens in ((True, 8192), (False, 4096)):
                raw = await choose_client(deep=deep).chat(
                    messages,
                    temperature=0.3,
                    max_tokens=max_tokens,
                    response_format={"type": "json_object"},
                )
                try:
                    data = _parse_llm_json(raw)
                    break
                except (ValueError, json.JSONDecodeError):
                    logger.warning("Arena 预测 JSON 解析失败（deep=%s），回退重试", deep)
                    data = None
            if not data:
                entry["reason"] = "LLM 输出两次解析失败"
                summary["assets"].append(entry)
                summary["failed"] += 1
                continue
            probs = normalize_probabilities(data)
            summary_text = str(data.get("summary", "")).strip() or "概率分布判断见 reasoning"
            reasoning = str(data.get("reasoning", "")).strip() or json.dumps(probs, ensure_ascii=False)

            await submit_prediction(challenge_id, probs, summary_text, reasoning)
            entry.update(
                status="submitted",
                challenge_id=challenge_id,
                probabilities={k: round(v, 4) for k, v in probs.items()},
            )
            summary["submitted"] += 1
            logger.info(
                "Arena 每日预测已提交 %s: bearish=%.2f neutral=%.2f bullish=%.2f",
                asset,
                probs["bearish"],
                probs["neutral"],
                probs["bullish"],
            )
        except Exception as e:
            # 单资产失败不影响其他资产
            entry["reason"] = str(e)[:200]
            summary["failed"] += 1
            logger.warning("Arena 每日预测 %s 失败: %s", asset, e)
        summary["assets"].append(entry)

    logger.info(
        "Arena 每日预测完成（%s）: 提交 %d / 跳过 %d / 失败 %d",
        trigger,
        summary["submitted"],
        summary["skipped"],
        summary["failed"],
    )
    return summary


async def _arena_daily_loop():
    """后台巡检循环：按配置间隔发现新题并补交。"""
    while True:
        try:
            await run_arena_daily("scheduled")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Arena 每日预测巡检异常", exc_info=True)
        await asyncio.sleep(config.HEADLINE_ARENA_CHECK_INTERVAL)


def start_arena_daily():
    """启动 Arena 每日预测后台任务；未启用或凭据缺失时跳过并记录日志。"""
    if not config.HEADLINE_ARENA_ENABLED:
        logger.info("Headline Arena 未启用（HEADLINE_ARENA_ENABLED=false），跳过预测任务")
        return None
    agent_id, client_secret = _credentials()
    if not agent_id or not client_secret:
        logger.info("Headline Arena 凭据缺失（环境变量/credentials.json 均未提供），跳过预测任务")
        return None
    task = task_manager.create_task(_arena_daily_loop(), name="arena_daily")
    logger.info("Headline Arena 预测任务已启动（每 %s 秒巡检，资产: %s）", config.HEADLINE_ARENA_CHECK_INTERVAL, config.HEADLINE_ARENA_DAILY_ASSETS)
    return task
