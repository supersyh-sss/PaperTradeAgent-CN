"""LangGraph Agent 共享状态定义 - v2: Multi-agent thinking logs"""

import operator
from datetime import UTC, datetime, timedelta, timezone
from typing import Annotated, TypedDict

BJT = timezone(timedelta(hours=8))


def _fmt_bj(dt: datetime) -> str:
    """北京时间的 locale 无关格式化。

    不使用 strftime 拼中文后缀——Windows 非中文 locale（如 cp1252）下 C 层 strftime
    无法编码 CJK 字符会抛 UnicodeEncodeError，纯 f-string 不受影响。
    """
    return (
        f"{dt.year}-{dt.month:02d}-{dt.day:02d} {dt.hour:02d}:{dt.minute:02d} 北京时间"
    )


def _to_beijing_time(iso_str: str | None) -> str:
    """将前端 UTC ISO 格式时间转换为北京时间字符串"""
    if not iso_str:
        return _fmt_bj(datetime.now(BJT))
    try:
        # 处理UTC时间：前端 toISOString() 返回 "2026-08-11T11:12:00.000Z"
        normalized = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        # 确保时区感知
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        bj_dt = dt.astimezone(BJT)
        return _fmt_bj(bj_dt)
    except (ValueError, TypeError):
        return _fmt_bj(datetime.now(BJT))


def _current_time_context(frontend_iso: str | None) -> str:
    """生成完整时间上下文（年月日时分/星期/节日/休市/时段/下一交易日）。

    统一基于系统北京时间计算，保证各 Agent 拿到一致且完整的时间信息；
    仅在交易时间判断器不可用时回退到前端传入时间的简单格式。
    """
    try:
        from ..services.trading_time import TradingTimeChecker

        return TradingTimeChecker.full_time_context()
    except Exception:
        return _to_beijing_time(frontend_iso)


class AgentState(TypedDict):
    # User Layer
    user_input: str
    user_id: str
    conversation_id: str  # persistent conversation ID
    trace_id: str  # 全链路追踪 ID

    # Quant Researcher
    quant_assessment: dict | None  # LLM structured technical analysis output

    # Market Intelligence
    intelligence_assessment: dict | None  # LLM structured sentiment analysis output

    # Portfolio Monitor
    portfolio_assessment: dict | None  # LLM structured portfolio health assessment

    # Trade Executor
    executor_assessment: dict | None  # LLM structured trade plan assessment

    # Chief Strategist Output
    intent: str
    active_symbol: str | None
    active_name: str | None
    in_watchlist: bool
    watchlist: list[dict]
    needed_agents: list[
        str
    ]  # agents to invoke (e.g. ["quant_researcher", "market_intelligence"])
    needs_report: bool  # whether a comprehensive report is needed (set by chief)
    chief_response: (
        str  # direct response from chief for chat/watchlist (skips response_generator)
    )
    strategy_direction: str  # human-readable summary of chief's decision
    detail_level: str  # "detailed" | "brief" | "auto"
    validated_stock: bool  # whether the identified stock was validated via API
    time_horizon: str  # "short"(短线/分钟级) | "long"(中长线/日周月年)
    analyze_watchlist: bool  # 是否批量分析自选股列表（逐只量化扫描）

    # Task Planning (L3): 结构化任务计划 + 有界反思循环
    plan: dict | None  # {"goal", "intent", "steps": [{"agent","task"}], "reasoning"}
    retry_count: int  # 质量门触发的重试次数（上限 1 次，防死循环）

    # Data Layer
    market_data: dict[str, dict]
    kline_data: list[dict] | None
    technical_analysis: dict | None
    fundamental_analysis: dict | None  # 基本面估值（PE/PB/换手/市值等）

    # Market Intelligence
    market_intelligence: dict | None
    sentiment_score: float | None
    risk_alerts: list[dict] | None

    # Trade Layer
    trade_plan: dict | None
    trade_side: str | None
    trade_quantity: int | None
    is_trading_time: bool
    order_result: dict | None
    pending_action: dict | None  # 通用待确认操作（自选股增删/撤单/交易）
    direct_execute: bool  # 用户明确要求直接执行交易（跳过确认卡片）

    # Portfolio Layer
    portfolio_summary: dict | None
    has_positions: bool  # 用户是否有活跃持仓
    positions: list[dict]  # 持仓列表（供 chief 注入上下文）

    # Multi-Agent Thinking Logs
    agent_logs: list[dict]

    # Conversation Layer
    messages: Annotated[list[dict], operator.add]
    final_response: str
    timestamp: str

    # Time Awareness & History Management
    current_time: str | None  # 当前时间（ISO格式），用于Agent时间感知
    history_summary: str | None  # 历史会话摘要（长对话裁剪后生成）

    # Direct Agent Addressing
    direct_agent: str | None  # 用户直接寻址的目标 Agent key（如 "quant_researcher"）
    mentioned_agents: str | None  # 多Agent寻址时所有匹配的key列表（逗号分隔）
    agent_chat_mode: bool  # Agent 自由对话模式（非分析任务，纯聊天）
    agent_chat_response: list[dict] | None  # Agent 聊天回复（支持多条消息）

    # Harness Engineering Framework
    harness_context: str | None  # REPL Read阶段组装的上下文
    harness_metrics: dict | None  # 当前会话度量快照
    safety_verified: bool  # 安全门控是否已通过
    execution_logs: list[dict] | None  # 执行日志（审计追踪）


def create_initial_state(
    user_input: str,
    user_id: str = "default",
    conversation_id: str = "",
    history_messages: list[dict] | None = None,
    current_time: str | None = None,
) -> AgentState:
    """Create initial state for a new conversation turn."""
    if not conversation_id:
        conversation_id = f"conv_{datetime.now(BJT).strftime('%Y%m%d_%H%M%S_%f')}"

    msgs = list(history_messages) if history_messages else []

    return AgentState(
        user_input=user_input,
        user_id=user_id,
        conversation_id=conversation_id,
        trace_id="",
        intent="chat",
        active_symbol=None,
        active_name=None,
        in_watchlist=False,
        watchlist=[],
        needed_agents=[],
        needs_report=False,
        chief_response="",
        strategy_direction="",
        validated_stock=False,
        detail_level="auto",
        time_horizon="long",
        analyze_watchlist=False,
        plan=None,
        retry_count=0,
        quant_assessment=None,
        intelligence_assessment=None,
        portfolio_assessment=None,
        executor_assessment=None,
        market_data={},
        kline_data=None,
        technical_analysis=None,
        fundamental_analysis=None,
        market_intelligence=None,
        sentiment_score=None,
        risk_alerts=None,
        trade_plan=None,
        trade_side=None,
        trade_quantity=None,
        is_trading_time=False,
        order_result=None,
        pending_action=None,
        direct_execute=False,
        portfolio_summary=None,
        has_positions=False,
        positions=[],
        agent_logs=[],
        messages=msgs,
        final_response="",
        timestamp=datetime.now(BJT).isoformat(),
        current_time=_current_time_context(current_time),
        history_summary=None,
        harness_context=None,
        harness_metrics=None,
        safety_verified=False,
        execution_logs=None,
        direct_agent=None,
        agent_chat_mode=False,
        agent_chat_response=None,
    )
