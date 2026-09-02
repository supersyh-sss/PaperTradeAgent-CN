"""Agent 共享工具函数 — 消除 6 个 Agent 文件中的重复代码"""

import json as json_mod
import logging
import random
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from ..services.agent_memory import (
    compute_query_hash,
    get_agent_memory,
    save_agent_memory,
)
from ..services.trading_time import TradingTimeChecker
from .prompts import AGENT_PROFILES
from .state import AgentState

BJT = timezone(timedelta(hours=8))
logger = logging.getLogger(__name__)


def build_agent_log(
    agent_key: str,
    content: str,
    *,
    is_chat_mode: bool = False,
    chat_messages: list | None = None,
) -> dict:
    """统一构建 agent_log 字典

    Args:
        agent_key: Agent 标识（如 "quant_researcher"）
        content: 文本内容
        is_chat_mode: 是否为对话模式
        chat_messages: 对话模式的多条消息列表

    Returns:
        标准 agent_log 字典
    """
    profile = AGENT_PROFILES.get(agent_key, {})
    log = {
        "agent": agent_key,
        "emoji": profile.get("emoji", ""),
        "name_cn": profile.get("name_cn", agent_key),
        "color": profile.get("color", "#6366f1"),
        "content": content,
        "timestamp": datetime.now(BJT).isoformat(),
    }
    if is_chat_mode:
        log["is_chat_mode"] = True
        log["chat_messages"] = chat_messages or []
    return log


def append_agent_log(state: AgentState, agent_log: dict):
    """向 state 追加 agent_log（统一入口）"""
    state.setdefault("agent_logs", []).append(agent_log)


# 各 Agent 在输出主消息后，按概率追加的补充消息池（人格化、贴合角色）
AGENT_FOLLOWUP_POOLS = {
    "chief_strategist": [
        "另外提醒一下，如果你需要具体的交易建议，可以随时让我协调对应Agent。",
        "补充一句：以上判断基于当前信息，市场变化快，建议定期回顾。",
    ],
    "quant_researcher": [
        "技术指标只反映过去，不代表未来走势，仅供参考。",
        "如果你关注某个具体指标，我可以单独分析。",
    ],
    "market_intelligence": [
        "市场情绪变化很快，建议持续关注后续消息。",
        "如果有新的公告或政策发布，我会第一时间跟进分析。",
    ],
    "trade_executor": [
        "成交后我会自动更新你的持仓和资金，不用担心。",
        "有价格笼子或涨跌停限制的话我会提前提醒你。",
    ],
    "portfolio_monitor": [
        "建议定期审视持仓集中度，避免单票风险过大。",
        "如果某只票回撤接近止损线，我会主动预警。",
    ],
}
FALLBACK_FOLLOWUPS = [
    "有什么需要深入讨论的，随时告诉我。",
    "如果还有其他问题，请随时追问。",
]

# 追加消息概率（增加自由度）
FOLLOWUP_PROBABILITY = 0.4


def maybe_attach_followup(
    agent_log: dict,
    agent_key: str,
    probability: float = FOLLOWUP_PROBABILITY,
) -> dict:
    """按概率给标准 Agent 的 agent_log 附加一条 follow-up 消息。

    返回一个新的 dict（不改动原值），仅当命中概率时附带 "followups" 字段。
    对话模式（is_chat_mode）已有独立的 followup 逻辑，此处跳过。
    """
    if agent_log.get("is_chat_mode"):
        return agent_log
    if random.random() >= probability:
        return agent_log
    pool = AGENT_FOLLOWUP_POOLS.get(agent_key, FALLBACK_FOLLOWUPS)
    log = dict(agent_log)
    log["followups"] = [random.choice(pool)]
    return log


async def load_cached_or_call_llm(
    state: AgentState,
    agent_key: str,
    symbol: str | None,
    user_input: str,
    llm_client: Any,
    build_messages: Callable[[], list[dict]],
    *,
    check_trading: bool = True,
    max_tokens: int = 1024,
    temperature: float = 0.1,
) -> dict | None:
    """缓存或 LLM 调用模式（统一入口，替代 4 个 Agent 文件中的重复逻辑）

    Args:
        state: Agent 状态
        agent_key: Agent 标识
        symbol: 股票代码（None=无个股）
        user_input: 用户输入
        llm_client: LLM 客户端（需有 chat_json 方法）
        build_messages: 构建消息列表的回调
        check_trading: 是否仅在非交易时段使用缓存
        max_tokens: LLM 最大 token
        temperature: LLM 温度

    Returns:
        LLM 返回的 dict，或 None（解析失败）
    """
    user_id = state.get("user_id", "default")
    query_hash = compute_query_hash(user_input, agent_key, symbol)
    is_trading = TradingTimeChecker.is_trading_time()

    # 非交易时段尝试缓存
    cached = None
    if check_trading and not is_trading:
        try:
            cached_raw = await get_agent_memory(
                user_id, agent_key, symbol, query_hash, query=user_input
            )
            if cached_raw:
                cached = json_mod.loads(cached_raw)
        except Exception as e:
            logger.warning("缓存读取失败 [%s/%s]: %s", agent_key, symbol, e)

    if cached and (not check_trading or not is_trading):
        return cached

    # 调用 LLM
    try:
        messages = build_messages()
        llm_result = await llm_client.chat_json(
            messages, temperature=temperature, max_tokens=max_tokens
        )
        if llm_result.get("parse_error"):
            raise ValueError(f"LLM returned non-JSON for {agent_key}")

        # 保存到缓存（仅在非交易时段）
        if check_trading and not is_trading:
            try:
                await save_agent_memory(
                    user_id,
                    agent_key,
                    symbol,
                    query_hash,
                    json_mod.dumps(llm_result, ensure_ascii=False, default=str),
                    query=user_input,
                )
            except Exception as e:
                logger.warning("缓存保存失败 [%s/%s]: %s", agent_key, symbol, e)

        return llm_result
    except Exception:
        logger.exception("LLM 调用失败 [%s/%s]", agent_key, symbol or "N/A")
        return None


def safe_int(value: Any) -> int | None:
    """将 LLM/外部输入安全转为 int；无法转换返回 None。

    覆盖 str('100'/'100.0')、float、int 等常见形态，避免后续与 int 做
    ``<``/``>`` 比较时抛 ``TypeError: '<' not supported between int and str``。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        s = value.strip()
        try:
            return int(float(s))
        except (ValueError, TypeError):
            return None
    return None


def join_lines(title: str, *body_lines: str) -> str:
    """构建 agent_log 文本内容的标准格式

    示例:
        join_lines("对茅台完成分析：", "● 价格：1000", "● RSI：55")
        → "对茅台完成分析：\n\n● 价格：1000\n● RSI：55"
    """
    parts = [title, ""]
    parts.extend(body_lines)
    return "\n".join(parts)
