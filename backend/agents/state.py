"""LangGraph Agent 共享状态定义 - v2: Multi-agent thinking logs"""
from typing import TypedDict, List, Dict, Optional, Annotated
from datetime import datetime, timezone, timedelta
import operator

BJT = timezone(timedelta(hours=8))


def _to_beijing_time(iso_str: str | None) -> str:
    """将前端 UTC ISO 格式时间转换为北京时间字符串"""
    if not iso_str:
        return datetime.now(BJT).strftime("%Y-%m-%d %H:%M 北京时间")
    try:
        # 处理UTC时间：前端 toISOString() 返回 "2026-08-11T11:12:00.000Z"
        normalized = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        # 确保时区感知
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        bj_dt = dt.astimezone(BJT)
        return bj_dt.strftime("%Y-%m-%d %H:%M 北京时间")
    except (ValueError, TypeError):
        return datetime.now(BJT).strftime("%Y-%m-%d %H:%M 北京时间")


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
    quant_assessment: Optional[dict]        # LLM structured technical analysis output

    # Market Intelligence
    intelligence_assessment: Optional[dict]  # LLM structured sentiment analysis output

    # Portfolio Monitor
    portfolio_assessment: Optional[dict]     # LLM structured portfolio health assessment

    # Trade Executor
    executor_assessment: Optional[dict]      # LLM structured trade plan assessment

    # Chief Strategist Output
    intent: str
    active_symbol: Optional[str]
    active_name: Optional[str]
    in_watchlist: bool
    watchlist: List[dict]
    needed_agents: List[str]       # agents to invoke (e.g. ["quant_researcher", "market_intelligence"])
    needs_report: bool             # whether a comprehensive report is needed (set by chief)
    chief_response: str            # direct response from chief for chat/watchlist (skips response_generator)
    strategy_direction: str        # human-readable summary of chief's decision
    detail_level: str              # "detailed" | "brief" | "auto"
    validated_stock: bool          # whether the identified stock was validated via API
    time_horizon: str              # "short"(短线/分钟级) | "long"(中长线/日周月年)
    analyze_watchlist: bool        # 是否批量分析自选股列表（逐只量化扫描）

    # Task Planning (L3): 结构化任务计划 + 有界反思循环
    plan: Optional[dict]           # {"goal", "intent", "steps": [{"agent","task"}], "reasoning"}
    retry_count: int               # 质量门触发的重试次数（上限 1 次，防死循环）

    # Data Layer
    market_data: Dict[str, dict]
    kline_data: Optional[List[dict]]
    technical_analysis: Optional[dict]
    fundamental_analysis: Optional[dict]   # 基本面估值（PE/PB/换手/市值等）

    # Market Intelligence
    market_intelligence: Optional[dict]
    sentiment_score: Optional[float]
    risk_alerts: Optional[List[dict]]

    # Trade Layer
    trade_plan: Optional[dict]
    trade_side: Optional[str]
    trade_quantity: Optional[int]
    is_trading_time: bool
    order_result: Optional[dict]
    pending_action: Optional[dict]       # 通用待确认操作（自选股增删/撤单/交易）
    direct_execute: bool                 # 用户明确要求直接执行交易（跳过确认卡片）

    # Portfolio Layer
    portfolio_summary: Optional[dict]
    has_positions: bool                 # 用户是否有活跃持仓
    positions: List[dict]               # 持仓列表（供 chief 注入上下文）

    # Multi-Agent Thinking Logs
    agent_logs: List[dict]

    # Conversation Layer
    messages: Annotated[List[dict], operator.add]
    final_response: str
    timestamp: str

    # Time Awareness & History Management
    current_time: Optional[str]  # 当前时间（ISO格式），用于Agent时间感知
    history_summary: Optional[str]  # 历史会话摘要（长对话裁剪后生成）

    # Direct Agent Addressing
    direct_agent: Optional[str]          # 用户直接寻址的目标 Agent key（如 "quant_researcher"）
    mentioned_agents: Optional[str]      # 多Agent寻址时所有匹配的key列表（逗号分隔）
    agent_chat_mode: bool                # Agent 自由对话模式（非分析任务，纯聊天）
    agent_chat_response: Optional[List[dict]]  # Agent 聊天回复（支持多条消息）

    # Harness Engineering Framework
    harness_context: Optional[str]       # REPL Read阶段组装的上下文
    harness_metrics: Optional[dict]      # 当前会话度量快照
    safety_verified: bool               # 安全门控是否已通过
    execution_logs: Optional[List[dict]] # 执行日志（审计追踪）


def create_initial_state(
    user_input: str,
    user_id: str = "default",
    conversation_id: str = "",
    history_messages: List[dict] = None,
    current_time: str = None,
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
