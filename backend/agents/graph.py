"""LangGraph 状态图 — v2: asyncio.gather 并行 Agent 编排"""

import logging
from langgraph.graph import StateGraph, END

from .state import AgentState, create_initial_state
from .chief_strategist import chief_strategist_node
from .trade_executor import trade_executor_node, execute_trade_node
from .response_generator import response_generator_node
from .dispatcher import dispatch_parallel_agents, PARALLEL_AGENTS

logger = logging.getLogger(__name__)

# Agent Chat Node (internal import for cleanliness)
import re
import random as _random
from datetime import datetime
from ..services.llm import deepseek
from ..services.db import get_watchlist, get_all_positions, get_account, get_active_orders_db
from ..services.trading_time import TradingTimeChecker
from .prompts import AGENT_CHAT_SYSTEM, AGENT_PROFILES

from .utils import AGENT_FOLLOWUP_POOLS as _AGENT_FOLLOWUP_POOLS, FALLBACK_FOLLOWUPS as _FALLBACK_FOLLOWUPS

_AGENT_SELF_INTRO = {
    "chief_strategist": "我是首席策略官，负责统筹全局、识别你的意图并调度各专业 Agent。你可以直接问我大盘、个股或持仓相关的问题。",
    "quant_researcher": "我是量化分析员，专注技术面分析（均线、MACD、RSI、布林带等）。你可以让我分析某只股票的技术走势。",
    "market_intelligence": "我是市场情报分析员，负责新闻、舆情与市场情绪。你可以问我大盘动态、板块表现或个股相关资讯。",
    "trade_executor": "我是交易执行员，负责制定交易计划、费用估算与风控校验。想买卖某只股票时可以直接告诉我。",
    "portfolio_monitor": "我是持仓风控官，关注你的仓位健康度、集中度风险与回撤。你可以问我持仓和盈亏情况。",
}


async def agent_chat_node(state: AgentState) -> AgentState:
    """@Agent 直接对话模式，注入用户持仓/自选/账户上下文（统一数据源）"""
    agent_key = state.get("direct_agent", "chief_strategist")
    logger.info("agent_chat_node:start agent=%s trace_id=%s", agent_key, state.get("trace_id", "-"))
    
    mentioned_agents_raw = state.get("mentioned_agents", "")
    user_input = state.get("user_input", "")
    history_summary = state.get("history_summary", "")
    user_id = state.get("user_id", "default")

    mentioned_agents_list = []
    if mentioned_agents_raw:
        mentioned_agents_list = [a.strip() for a in mentioned_agents_raw.split(",") if a.strip()]
        if len(mentioned_agents_list) > 1:
            agent_key = "chief_strategist"

    system_prompt = AGENT_CHAT_SYSTEM.get(agent_key, AGENT_CHAT_SYSTEM["chief_strategist"])
    agent_info = AGENT_PROFILES.get(agent_key, AGENT_PROFILES["chief_strategist"])

    # 使用统一数据源（DataFeed）获取上下文，确保数据一致性
    from ..services.data_feed import get_agent_context
    context_text = ""
    try:
        context_text = await get_agent_context(user_id)
    except Exception:
        # 降级：逐项 fallback
        context_parts = [f"User said: {user_input}"]
        try:
            watchlist = await get_watchlist(user_id)
            if watchlist:
                context_parts.append("用户自选: " + " | ".join(f"{w.get('name','')}({w.get('symbol','')})" for w in watchlist[:15]))
        except Exception: pass
        try:
            account = await get_account(user_id)
            if account:
                context_parts.append(f"账户: 可用{account.get('available_balance',0):.2f}, 总资产{account.get('total_assets',0):.2f}")
        except Exception: pass
        try:
            positions = await get_all_positions(user_id)
            if positions:
                context_parts.append("持仓: " + " | ".join(f"{p.get('name','')} x{p.get('quantity',0)}" for p in positions[:10]))
        except Exception: pass
        try:
            active_orders = await get_active_orders_db(user_id)
            if active_orders:
                context_parts.append("活跃订单: " + " | ".join(f"{'买' if o.get('side')=='buy' else '卖'}{o.get('symbol','')} {o.get('price',0)}x{o.get('quantity',0)}" for o in active_orders[:5]))
        except Exception: pass
        if history_summary:
            context_parts.append(f"历史上下文: {history_summary}")
        context_text = "\n".join(context_parts)

    # 市场情报 Agent：注入实时新闻数据，防止 LLM 编造新闻
    if agent_key == "market_intelligence":
        try:
            from ..services.news_service import get_market_news
            news_result = await get_market_news()
            if news_result and news_result.get("data"):
                news_items = news_result["data"]
                real_news = [n for n in news_items if "暂无" not in n.get("title", "")]
                if real_news:
                    news_lines = ["\n=== REAL-TIME MARKET NEWS (use these, do NOT fabricate; publish time is marked) ==="]
                    for n in real_news[:8]:
                        pub_time = n.get('time', '')
                        time_part = f" [{pub_time}]" if pub_time else ""
                        news_lines.append(f"[{n.get('source','')}]{time_part} {n.get('title','')} — {n.get('summary','')[:100]}")
                    context_text += "\n".join(news_lines)
        except Exception:
            pass

    # 市场情报 Agent：注入大盘指数数据
    if agent_key == "market_intelligence":
        try:
            from ..services.indices import get_cached_indices, get_market_sentiment, get_cached_indices_time
            indices = get_cached_indices()
            sentiment = get_market_sentiment()
            if indices:
                idx_ts = get_cached_indices_time()
                ts_part = f" (as of {idx_ts})" if idx_ts else ""
                idx_lines = [f"\n=== CURRENT INDEX DATA{ts_part} ==="]
                idx_display = {"sh000001": "上证指数", "sz399001": "深证成指", "sz399006": "创业板指", "sh000688": "科创50", "sh000300": "沪深300"}
                for sym, name in idx_display.items():
                    d = indices.get(sym, {})
                    if d:
                        idx_lines.append(f"{name}: {d.get('last_price', d.get('price', 'N/A'))} ({d.get('change_pct', 0):+.2f}%)")
                idx_lines.append(f"市场情绪: {sentiment.get('sentiment', 'unknown')} (score: {sentiment.get('score', 0)})")
                context_text += "\n".join(idx_lines)
        except Exception:
            pass

    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": f"User said: {user_input}\n\n{context_text}"}]
    try:
        raw = await deepseek.chat(messages, temperature=0.7, max_tokens=600)
        parts = re.split(r'---\s*FOLLOWUP\s*---', raw)
        chat_msgs = [{"index": i, "content": p.strip(), "is_followup": i > 0} for i, p in enumerate(parts) if p.strip()]
        if not chat_msgs:
            chat_msgs = [{"index": 0, "content": _AGENT_SELF_INTRO.get(agent_key, f"我是{agent_info['name_cn']}，有什么可以帮你的？"), "is_followup": False}]
        elif len(chat_msgs) == 1 and _random.random() < 0.4:
            pool = _AGENT_FOLLOWUP_POOLS.get(agent_key, _FALLBACK_FOLLOWUPS)
            chat_msgs.append({"index": 1, "content": _random.choice(pool), "is_followup": True})
        state["agent_chat_response"] = chat_msgs
    except Exception:
        state["agent_chat_response"] = [{"index": 0, "content": f"{agent_info['name_cn']} 暂时无法回应，请稍后再试。", "is_followup": False}]

    agent_log = {"agent": agent_key, "emoji": agent_info.get("emoji", ""), "name_cn": agent_info.get("name_cn", agent_key),
        "color": agent_info.get("color", "#6366f1"), "content": "", "timestamp": datetime.now().astimezone().isoformat(),
        "is_chat_mode": True, "chat_messages": state["agent_chat_response"]}
    state.setdefault("agent_logs", []).append(agent_log)
    logger.info("agent_chat_node:end agent=%s trace_id=%s", agent_key, state.get("trace_id", "-"))
    return state


# ═══════════════════════════════════════════════════════════
# 核心路由函数
# ═══════════════════════════════════════════════════════════

def _route_after_chief(state: AgentState) -> str:
    """首席策略后路由

    · direct_agent → agent_chat
    · 无需 Agent 且无需报告 → end (chat/watchlist)
    · 需要 Agent → dispatch_agents
    """
    intent = state.get("intent", "")
    needed = state.get("needed_agents", [])
    needs_report = state.get("needs_report", False)
    chief_response = state.get("chief_response", "")

    if intent == "direct_agent":
        return "agent_chat"

    # chat / watchlist never need agent dispatch — reply from chief_response or stream fallback
    if intent in ("chat", "watchlist"):
        return "end"

    if not needed:
        if needs_report or not chief_response:
            return "dispatch_agents"
        return "end"

    return "dispatch_agents"


def _route_after_dispatch(state: AgentState) -> str:
    """调度后路由：质量门 → 有界重试 → 交易/报告

    · 质量门发现降级/失败且未重试过 → retry_degraded（有界反思，上限 1 次）
    · trade intent 且有交易计划 → trade_executor → response_generator
    · 否则 → response_generator 直接出报告
    """
    intent = state.get("intent", "")
    needed = state.get("needed_agents", [])
    retry_count = state.get("retry_count", 0)

    # L3 质量门：检查调度完成的 Agent 输出质量
    degraded = _run_quality_gate(state)
    state["_degraded_agents"] = degraded

    # 有界反思循环：仅当存在降级 Agent 且尚未重试时，进入重试
    if degraded and retry_count < 1:
        return "retry_degraded"

    if intent == "trade" and "trade_executor" in needed:
        return "trade_executor"

    return "response_generator"


# ═══════════════════════════════════════════════════════════
# 并行调度节点
# ═══════════════════════════════════════════════════════════

async def dispatch_agents_node(state: AgentState) -> AgentState:
    """统一 Agent 调度节点

    1. 分类 needed_agents 为并行组和串行组
    2. 并行组通过 asyncio.gather 同时执行
    3. 串行组逐一执行
    """
    needed = state.get("needed_agents", [])
    logger.info("dispatch_agents_node:start agents=%s trace_id=%s", needed, state.get("trace_id", "-"))
    if not needed:
        return state

    parallel = [a for a in needed if a in PARALLEL_AGENTS]
    sequential = [a for a in needed if a not in PARALLEL_AGENTS]

    # 存储串行 Agent 供后续路由
    state["_sequential_agents"] = sequential

    # 并行执行
    if parallel:
        await dispatch_parallel_agents(state, parallel)

    logger.info("dispatch_agents_node:end agents=%s trace_id=%s", needed, state.get("trace_id", "-"))
    return state


def _run_quality_gate(state: AgentState) -> list:
    """检查调度完成的 Agent 输出质量，返回降级/失败的 Agent key 列表

    L3 落地：dispatcher 采用 asyncio.gather 在单节点内合并结果，
    quant_assessment 等字段不再丢失，质量门可安全判定并驱动重试。
    """
    from .quality_gate import check_agent_output, QualityStatus

    needed = state.get("needed_agents", [])
    degraded = []
    for agent_key in needed:
        # 仅检查并行调度完成的 Agent；串行节点（如 trade_executor）由独立节点执行，
        # 此时尚未产出结果，若在此检查会误判为空并触发错误重试。
        if agent_key not in PARALLEL_AGENTS:
            continue
        status, detail, _action = check_agent_output(agent_key, state)
        if status in (QualityStatus.FAILED, QualityStatus.DEGRADED):
            logger.warning("Quality gate flags %s: %s (%s)", agent_key, status, detail)
            degraded.append(agent_key)
    return degraded


async def retry_degraded_node(state: AgentState) -> AgentState:
    """质量门触发的有界重试节点：仅重跑降级/失败的 Agent（上限 1 次）"""
    degraded = state.get("_degraded_agents", [])
    state["retry_count"] = state.get("retry_count", 0) + 1
    logger.warning("retry_degraded_node: retry_count=%d agents=%s trace_id=%s",
                   state["retry_count"], degraded, state.get("trace_id", "-"))
    if degraded:
        await dispatch_parallel_agents(state, degraded)
    return state


# ═══════════════════════════════════════════════════════════
# 图构建
# ═══════════════════════════════════════════════════════════

def build_trading_graph() -> StateGraph:
    """构建 v2 Agent 编排图（dispatcher 模式）"""

    workflow = StateGraph(AgentState)

    workflow.add_node("chief_strategist", chief_strategist_node)
    workflow.add_node("dispatch_agents", dispatch_agents_node)
    workflow.add_node("retry_degraded", retry_degraded_node)
    workflow.add_node("trade_executor", trade_executor_node)
    workflow.add_node("execute_trade", execute_trade_node)
    workflow.add_node("response_generator", response_generator_node)
    workflow.add_node("agent_chat", agent_chat_node)

    workflow.set_entry_point("chief_strategist")

    workflow.add_conditional_edges("chief_strategist", _route_after_chief, {
        "dispatch_agents": "dispatch_agents",
        "agent_chat": "agent_chat",
        "end": END,
    })

    workflow.add_edge("agent_chat", END)

    workflow.add_conditional_edges("dispatch_agents", _route_after_dispatch, {
        "trade_executor": "trade_executor",
        "response_generator": "response_generator",
        "retry_degraded": "retry_degraded",
    })

    # 有界反思循环：重试后再次过质量门路由（retry_count 已 +1，不会死循环）
    workflow.add_conditional_edges("retry_degraded", _route_after_dispatch, {
        "trade_executor": "trade_executor",
        "response_generator": "response_generator",
        "retry_degraded": "retry_degraded",
    })

    workflow.add_edge("trade_executor", "response_generator")
    workflow.add_edge("response_generator", END)

    # 交易确认链路（绕过首席）
    workflow.add_edge("execute_trade", "response_generator")

    return workflow.compile()


trading_graph = build_trading_graph()
