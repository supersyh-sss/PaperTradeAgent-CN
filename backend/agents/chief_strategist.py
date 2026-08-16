"""首席策略官 - LLM 自主意图识别 + 任务拆解（LLM 主导分类，关键词仅作安全网兜底）"""
import logging
import re
import time as _time
from datetime import datetime

from .state import AgentState

logger = logging.getLogger(__name__)
from ..services.db import get_watchlist, is_in_watchlist, get_all_positions
from ..services.position_service import compute_sellable
from ..services.llm import deepseek
from ..services.symbol import pure_code
from .prompts import CHIEF_STRATEGIST_SYSTEM, CHIEF_STRATEGIST_USER_TEMPLATE, AGENT_PROFILES
from .utils import safe_int

# 意图中文映射
INTENT_CN_MAP = {
    "analyze": "分析", "trade": "交易", "query": "查询",
    "portfolio": "持仓", "watchlist": "自选股", "chat": "对话",
    "market": "市场概览", "cancel_order": "撤单",
}

AGENT_CN_MAP = {
    "quant_researcher": "量化分析（技术分析）",
    "market_intelligence": "市场情报（舆情分析）",
    "trade_executor": "交易执行（交易计划）",
    "portfolio_monitor": "持仓风控（仓位分析）",
}

# 意图 → 默认任务计划模板（L3：LLM 未给出 needed_agents 时的"最小计划"兜底）
# 这是"声明了每步任务的结构化计划"，而非"硬编码路由映射"——needed_agents 由计划推导
_INTENT_PLAN_TEMPLATE = {
    "analyze": [
        {"agent": "quant_researcher", "task": "技术面分析（均线/MACD/RSI）"},
        {"agent": "market_intelligence", "task": "消息面与市场情绪分析"},
    ],
    "trade": [
        {"agent": "quant_researcher", "task": "技术面分析"},
        {"agent": "market_intelligence", "task": "消息面与风险信号分析"},
        {"agent": "trade_executor", "task": "制定交易计划与风控校验"},
    ],
    "market": [
        {"agent": "market_intelligence", "task": "市场概览与情绪扫描"},
    ],
    "query": [
        {"agent": "quant_researcher", "task": "个股技术面查询"},
    ],
    "portfolio": [
        {"agent": "portfolio_monitor", "task": "持仓健康度与集中度风险分析"},
    ],
}


def _build_plan(user_input: str, intent: str, needed_agents: list, reasoning: str) -> dict:
    """构建结构化任务计划（L3 可解释规划）"""
    steps = []
    for a in needed_agents:
        steps.append({"agent": a, "task": AGENT_CN_MAP.get(a, a)})
    return {
        "goal": user_input,
        "intent": intent,
        "steps": steps,
        "reasoning": reasoning or "",
    }


async def chief_strategist_node(state: AgentState) -> AgentState:
    """首席策略官节点：LLM 自主意图识别 + 自选股校验 + 任务拆解
    所有分类决策由大模型自主完成，无任何关键词兜底或硬编码校正。"""
    user_input = state["user_input"]
    user_id = state.get("user_id", "default")

    # 1. 加载自选股
    watchlist = await get_watchlist(user_id)
    state["watchlist"] = watchlist

    # 1b. 检查持仓状态
    positions = await get_all_positions(user_id)
    has_positions = bool(positions and len(positions) > 0)
    state["has_positions"] = has_positions
    state["positions"] = positions

    # 1c. 注入当前交易时间状态（非交易流也需感知）
    from ..services.trading_time import TradingTimeChecker
    state["is_trading_time"] = TradingTimeChecker.is_trading_time()

    # 2. 本地规则提取股票代码（辅助 LLM，不作为分类依据）
    symbol, name = _extract_symbol(user_input, watchlist)

    # 3. LLM 自主意图识别 — 唯一分类路径
    trade_side = None
    trade_quantity = 0
    direct_agent = None                             # 直接寻址目标
    mentioned_agents = ""                           # 多Agent寻址列表
    try:
        user_prompt = await _build_user_prompt(user_input, watchlist, state, user_id)

        messages = [
            {"role": "system", "content": CHIEF_STRATEGIST_SYSTEM},
            {"role": "user", "content": user_prompt},
        ]
        llm_result = await deepseek.chat_json(messages, temperature=0.1, max_tokens=1024)

        intent = llm_result.get("intent", "chat")

        # LLM 分类为 chat 时，用关键词兜底检测明显误判（不覆盖 LLM 自身的有意义分类）
        if intent == "chat":
            intent = _recover_from_chat(user_input)
        llm_symbol = llm_result.get("stock_symbol")
        llm_name = llm_result.get("stock_name")
        trade_side = llm_result.get("trade_side")
        trade_quantity = safe_int(llm_result.get("trade_quantity"))
        needs_report = llm_result.get("needs_report", False)
        needed_agents = llm_result.get("needed_agents", [])
        reasoning_chain = llm_result.get("reasoning_chain", "")
        chat_reply = llm_result.get("chat_reply", "")
        cancel_order_ids = llm_result.get("cancel_order_ids") or []
        if isinstance(cancel_order_ids, str):
            cancel_order_ids = [cancel_order_ids] if cancel_order_ids else []
        watchlist_action = llm_result.get("watchlist_action", "") or ""

        # Direct Agent Addressing - Local keyword match as primary (deterministic, no LLM hallucination)
        direct_agent, mentioned_agents = _detect_direct_agent(user_input)
        if direct_agent:
            intent = "direct_agent"
            needed_agents = [direct_agent]
            needs_report = False
            chat_reply = ""
        elif intent == "direct_agent" and llm_result.get("direct_agent"):
            # LLM detected direct agent addressing
            direct_agent = llm_result["direct_agent"]
            needed_agents = [direct_agent] if direct_agent else []
            mentioned_agents = direct_agent
            needs_report = False
            chat_reply = ""

        state["needs_report"] = needs_report
        state["needed_agents"] = needed_agents if isinstance(needed_agents, list) else []

        # L3 任务规划：LLM 未给出 needed_agents 时，用"最小计划模板"兜底（而非硬编码路由）
        if not state["needed_agents"] and intent not in ("chat", "watchlist", "direct_agent", "cancel_order"):
            steps = _INTENT_PLAN_TEMPLATE.get(intent, [])
            state["needed_agents"] = [s["agent"] for s in steps]
        # When needs_report=false, always use chat_reply — agents provide supplemental info
        # in agent_log bubbles, and chief_reply serves as the user-facing conversational answer.
        # When needs_report=true, chat_reply is intentionally empty (full report from agent synthesis).
        state["chief_response"] = chat_reply if not needs_report else ""
        state["strategy_direction"] = reasoning_chain
        state["detail_level"] = llm_result.get("detail_level", "auto")

        # cancel_order: 生成待确认操作（不直接撤单，等用户确认）
        if intent == "cancel_order" and cancel_order_ids:
            state["pending_action"] = {
                "type": "cancel_order",
                "message": f"确认撤销以下 {len(cancel_order_ids)} 笔委托吗？",
                "data": {"order_ids": [str(x) for x in cancel_order_ids]},
            }
            state["chief_response"] = f"已识别到 {len(cancel_order_ids)} 笔待撤销委托，请确认是否继续。"
            intent = "cancel_order"
            needs_report = False
            state["needs_report"] = False
            state["needed_agents"] = []

        # LLM 识别到的股票信息优先
        if llm_symbol:
            if not symbol:
                symbol = llm_symbol
                name = llm_name or llm_symbol
            # Validate LLM-identified stock via Tencent API
            try:
                from ..services.tencent_api import tencent_api
                api_symbol = tencent_api._make_code(symbol) if not symbol.startswith(("sh", "sz", "bj")) else symbol
                rt_data = await tencent_api.get_realtime([api_symbol])
                pure_sym = pure_code(symbol)
                if rt_data and pure_sym in rt_data and rt_data[pure_sym].get("name"):
                    name = rt_data[pure_sym]["name"]
                    state["validated_stock"] = True
                else:
                    symbol = None
                    name = None
                    state["validated_stock"] = False
            except Exception:
                logger.warning("行情校验失败，认定无效", exc_info=True)
                state["validated_stock"] = False

        # 分析自选股列表：确定性路由到量化分析（逐只扫描，避免误判为市场/查看列表）
        if not symbol and _detect_analyze_watchlist(user_input) and watchlist:
            intent = "analyze"
            needed_agents = ["quant_researcher", "market_intelligence"]
            needs_report = False
            chat_reply = ""
            state["needs_report"] = False
            state["needed_agents"] = needed_agents
            state["chief_response"] = ""
            state["analyze_watchlist"] = True

        # watchlist 增删：生成待确认操作（等用户确认后再执行，不直接改数据）
        if symbol and (intent == "watchlist" or watchlist_action in ("add", "remove")):
            if not watchlist_action:
                if any(kw in user_input for kw in ("添加", "加自选", "加入", "关注")):
                    watchlist_action = "add"
                elif any(kw in user_input for kw in ("移除", "删除", "删", "取消关注", "移出")):
                    watchlist_action = "remove"
            if watchlist_action in ("add", "remove"):
                _code = pure_code(symbol)
                state["pending_action"] = {
                    "type": f"watchlist_{watchlist_action}",
                    "message": f"确认{'添加' if watchlist_action == 'add' else '移除'}自选股 {name or _code}({_code}) 吗？",
                    "data": {"symbol": _code, "name": name or _code},
                }
                state["chief_response"] = f"将{'添加' if watchlist_action == 'add' else '移除'} {name or _code}({_code})，请确认是否继续。"
                intent = "watchlist"
                needs_report = False
                state["needs_report"] = False
                state["needed_agents"] = []

        # 查看自选股列表：确定性展示（避免 LLM 因盘前/无价格而误判"没数据"）
        if intent == "watchlist" and not symbol and watchlist_action not in ("add", "remove"):
            if watchlist:
                state["chief_response"] = "当前自选股：\n" + "\n".join(
                    f"  {w.get('name', '')}（{w.get('symbol', '')}）" for w in watchlist[:20]
                )
            else:
                state["chief_response"] = "你的自选股列表还是空的，可以对某只股票说「加入自选」来添加。"
            needs_report = False
            state["needs_report"] = False
            state["needed_agents"] = []

        # 板块/行业/概念查询安全网：未识别到具体股票时，统一路由到市场情报
        if not symbol and any(kw in user_input for kw in ("板块", "行业", "概念", "题材")):
            intent = "market"
            needed_agents = ["market_intelligence"]
            needs_report = False
            chat_reply = ""
            state["needs_report"] = False
            state["needed_agents"] = ["market_intelligence"]
            state["chief_response"] = ""

        # 持仓/盈亏分析安全网：无具体股票且非交易动作时，统一路由到持仓风控
        if (not symbol and intent in ("chat", "analyze", "query")
                and any(kw in user_input for kw in ("持仓", "仓位", "亏损", "盈亏", "账户", "我的股票"))):
            intent = "portfolio"
            needed_agents = ["portfolio_monitor"]
            needs_report = False
            chat_reply = ""
            state["needs_report"] = False
            state["needed_agents"] = ["portfolio_monitor"]
            state["chief_response"] = ""

        if trade_side is not None and trade_side:
            state["trade_side"] = trade_side
        else:
            # 安全网：LLM 未识别方向时，从用户输入兜底提取买卖方向
            _side = _extract_trade_side(user_input)
            if _side:
                trade_side = _side
                state["trade_side"] = _side
        if trade_quantity:
            state["trade_quantity"] = trade_quantity

        # "直接买入/直接卖出" → 跳过确认卡片，直接执行
        if trade_side and _is_direct_execute(user_input):
            state["direct_execute"] = True

    except Exception as e:
        # Fallback: try once more with simpler prompt (no watchlist, no history)
        try:
            simple_prompt = f"User: {user_input}\nTime: {_time.strftime('%Y-%m-%d %H:%M')} Beijing"
            messages_simple = [
                {"role": "system", "content": CHIEF_STRATEGIST_SYSTEM},
                {"role": "user", "content": simple_prompt},
            ]
            llm_result = await deepseek.chat_json(messages_simple, temperature=0.1, max_tokens=1024)
            intent = llm_result.get("intent", "chat")
            needed_agents = llm_result.get("needed_agents", [])
            needs_report = llm_result.get("needs_report", False)
            chat_reply = llm_result.get("chat_reply", "你好！有什么可以帮你的？")
            # Restore direct_agent if regex matched (deterministic detection should survive retry)
            if direct_agent:
                intent = "direct_agent"
                needed_agents = [direct_agent]
                needs_report = False
                chat_reply = ""
            state["needs_report"] = needs_report
            state["needed_agents"] = needed_agents if isinstance(needed_agents, list) else []
            state["chief_response"] = chat_reply if not needs_report else ""
            state["strategy_direction"] = f"LLM retry OK: {llm_result.get('reasoning_chain', '')}"
        except Exception as e2:
            logger.error(f"Chief LLM failed (both attempts): first={str(e)[:100]}, retry={str(e2)[:100]}")
            intent = "chat"
            # Preserve direct_agent detection even in double-fail
            if direct_agent:
                intent = "direct_agent"
                state["needed_agents"] = [direct_agent]
                state["needs_report"] = False
                state["chief_response"] = ""
            else:
                state["needs_report"] = False
                state["needed_agents"] = []
                state["chief_response"] = "你好！我是A股模拟交易助手，可以帮你分析股票、模拟交易、查看持仓。请问有什么可以帮你的？"
            state["strategy_direction"] = f"LLM调用失败，默认chat回复"

    # 4. 校验自选股
    in_watchlist = False
    if symbol:
        in_watchlist = await is_in_watchlist(user_id, symbol)

    state["intent"] = intent
    state["active_symbol"] = symbol
    state["active_name"] = name
    state["in_watchlist"] = in_watchlist
    state["direct_agent"] = direct_agent
    state["mentioned_agents"] = mentioned_agents
    state["agent_chat_mode"] = (intent == "direct_agent")
    state["time_horizon"] = _detect_time_horizon(user_input)

    # 确定性覆盖：只有用户明确要求报告时才允许 needs_report=true。
    # 防止 LLM 偶发把"看看持仓/看看茅台"这类轻量查看误判为报告（报告应由明确意图触发）。
    if state.get("needs_report") and not _explicit_report_request(user_input):
        state["needs_report"] = False
        # needs_report 被 LLM 置 true 时 chat_reply 通常为空；此处交由
        # _generate_non_report_reply 基于 Agent 输出合成对话式回复。

    # L3 可解释规划：构建结构化计划（goal/steps/reasoning）并挂载到状态
    state["plan"] = _build_plan(
        user_input, intent, state.get("needed_agents", []), state.get("strategy_direction", "")
    )

    # 5. Emit agent_log
    intent_cn = INTENT_CN_MAP.get(intent, intent)
    needed = state.get("needed_agents", [])
    agent_log_content = _build_chief_strategist_log(
        user_input, intent, intent_cn, name, symbol, in_watchlist,
        trade_side, trade_quantity, watchlist, needed, state.get("strategy_direction", "")
    )
    agent_log = {
        "agent": "chief_strategist",
        "emoji": AGENT_PROFILES["chief_strategist"]["emoji"],
        "name_cn": AGENT_PROFILES["chief_strategist"]["name_cn"],
        "color": AGENT_PROFILES["chief_strategist"]["color"],
        "content": agent_log_content,
        "timestamp": datetime.now().astimezone().isoformat(),
    }
    state.setdefault("agent_logs", []).append(agent_log)

    return state


def _recover_from_chat(user_input: str) -> str:
    """关键词兜底：仅在 LLM 返回 chat 时修正最明确的交易/自选指令误判。
    极度精简兜底规则，LLM 是主导分类器，此处仅作为最后安全网。"""
    text = user_input.strip()

    # 明确的撤单指令（先于交易指令，避免「取消挂单」被「挂单」误判为 trade）
    if any(kw in text for kw in ["撤单", "取消挂单", "撤销委托", "撤销订单", "取消订单", "撤掉"]):
        return "cancel_order"

    # 明确的交易指令（动词+动作，LLM 极少误判，纯安全网）
    if any(kw in text for kw in ["买入", "卖出", "下单", "挂单", "开仓", "平仓", "建仓"]):
        return "trade"

    # 明确的自选管理指令
    if any(kw in text for kw in ["添加自选", "移除自选", "删除自选"]):
        return "watchlist"

    # 市场/行情查询（用户要求看行情/大盘但未指定个股，含板块/行业/概念）
    if any(kw in text for kw in ["查看行情", "行情", "大盘", "市场走势", "市场怎么样", "今天大盘", "指数", "市场概览", "整体走势", "涨跌情况",
                                  "看大盘", "看指数", "看行情", "股市行情", "A股整体", "板块", "行业", "概念", "题材"]):
        return "market"

    # 持仓查询
    if any(kw in text for kw in ["查看持仓", "持仓情况", "仓位", "仓位怎么样", "仓位如何", "我的股票", "盈亏", "账户"]):
        return "portfolio"

    return "chat"


def _detect_analyze_watchlist(user_input: str) -> bool:
    """识别「分析自选股」意图：既提到自选股，又带有分析/走势/技术等意图词。

    与「看看自选股」（纯查看列表）区分：只有明确分析意图才路由到量化分析。
    """
    text = (user_input or "").strip()
    has_watch = "自选" in text
    has_analyze = any(kw in text for kw in ("分析", "走势", "技术", "研判", "评估", "解读", "信号", "扫描"))
    return has_watch and has_analyze


def _extract_trade_side(user_input: str) -> str:
    """从用户输入兜底提取交易方向（买/卖），仅在 LLM 未识别方向时使用"""
    text = user_input.strip()
    if any(kw in text for kw in ["卖出", "卖", "清仓", "减持", "减仓", "平仓", "出"]):
        return "SELL"
    if any(kw in text for kw in ["买入", "买", "建仓", "加仓", "增持", "开仓"]):
        return "BUY"
    return ""


# 明确要求生成完整分析报告的触发词（与 prompts.py 中 NEEDS_REPORT 显式触发词保持一致）
_REPORT_TRIGGERS = (
    "分析报告", "详细分析", "深度分析", "研判", "全面评估",
    "写个报告", "出个报告", "全面梳理", "帮我分析一下", "报告",
)


def _explicit_report_request(user_input: str) -> bool:
    """判断用户是否明确要求生成完整分析报告（确定性覆盖，防止 LLM 偶发误判）。

    仅当用户使用明确的报告触发词时才返回 True；"看看持仓""看看茅台"等轻量查看
    一律返回 False，即使 LLM 误判 needs_report=true 也会被校正为 False。
    """
    text = (user_input or "").strip()
    return any(kw in text for kw in _REPORT_TRIGGERS)


# 直接执行交易的触发词（跳过确认卡片，立即下单）
_DIRECT_EXECUTE_TRIGGERS = ("直接", "立即", "马上", "立刻")


def _is_direct_execute(user_input: str) -> bool:
    """判断用户是否明确要求直接执行交易（如「直接买入茅台」「立即卖出」）。"""
    text = (user_input or "").strip()
    return any(kw in text for kw in _DIRECT_EXECUTE_TRIGGERS)


# 交易周期识别：只做短线（分钟级 3-5 分钟）与中长线（日/月/年），不做超短线
_SHORT_HORIZON_KW = ("短线", "日内", "分钟", "做t", "快进快出", "当日", "当天", "今天买", "今天卖")
_LONG_HORIZON_KW = ("中长线", "长线", "长期", "波段", "趋势", "持有", "中线", "中长", "价值", "布局", "月", "年")


def _detect_time_horizon(user_input: str) -> str:
    """识别用户交易周期：短线 → "short"；其余默认中长线 → "long"。

    仅做两类：短线（分钟级）与中长线（日/月/年），不做超短线。
    """
    text = (user_input or "").strip().lower()
    if any(kw in text for kw in _SHORT_HORIZON_KW):
        return "short"
    return "long"


async def _build_user_prompt(user_input: str, watchlist: list, state: dict, user_id: str) -> str:
    """构建注入所有上下文的用户提示"""
    watchlist_str = ", ".join(
        f"{w.get('name', '')}({w.get('symbol', '')})" for w in watchlist
    ) if watchlist else "empty"

    # 注入持仓上下文（含账户余额）
    position_lines = []
    
    # 获取账户余额
    try:
        from ..services.db import get_account
        account = await get_account(user_id)
        if account:
            bal = account.get("balance", 0)
            ta = account.get("total_assets", 0)
            position_lines.append(f"\nACCOUNT: 可用现金 {bal:,.2f}元, 总资产 {ta:,.2f}元")
    except Exception:
        logger.warning("账户余额获取失败", exc_info=True)
    
    positions = state.get("positions", [])
    if positions:
        from ..services.live_prices import get_cached_price, get_live_cache_time
        _quote_ts = get_live_cache_time()
        _ts_suffix = f"（行情时间 {_quote_ts}）" if _quote_ts else ""
        position_lines.append(f"\nUSER POSITIONS (LIVE){_ts_suffix}:")
        for pos in positions:
            sym = pos.get("symbol", "")
            name = pos.get("name", "")
            qty = pos.get("quantity", 0)
            avg = pos.get("avg_cost", 0)
            t1_qty, _ = compute_sellable(pos)
            if qty <= 0:
                continue
            cache = get_cached_price(sym)
            cur_price = float(cache.get("last_price", 0)) if cache else 0
            pnl = ((cur_price - avg) * qty) if cur_price > 0 and avg > 0 else 0
            pnl_pct = ((cur_price - avg) / avg * 100) if avg > 0 and cur_price > 0 else 0
            position_lines.append(
                f"  {name or sym}({sym}): {qty}股 成本{avg:.3f} "
                f"现价{'%.3f' % cur_price if cur_price > 0 else 'N/A'} "
                f"盈亏{'%+.2f' % pnl_pct}% "
                f"(T+1冻结{t1_qty}股)"
            )
    else:
        position_lines.append("\nUSER POSITIONS: no active positions (all cash)")

    current_time = state.get("current_time", "")
    history_summary = state.get("history_summary", "")

    user_prompt = CHIEF_STRATEGIST_USER_TEMPLATE.format(
        user_input=user_input,
        watchlist_str=watchlist_str,
        current_time=current_time or "unknown",
        history_summary=history_summary or "(This is a fresh conversation, no prior context)",
    )
    user_prompt += "\n" + "\n".join(position_lines)

    # 注入今日交易状态（是否休市），供回答"现在几点/今天几号"等时间问题时顺带说明
    try:
        from ..services.trading_time import TradingTimeChecker
        user_prompt += f"\nMARKET STATUS: {TradingTimeChecker.market_status_text()}"
    except Exception:
        logger.warning("交易状态注入失败", exc_info=True)

    # 注入大盘指数和市场情绪数据
    try:
        from ..services.indices import get_cached_indices, get_market_sentiment, get_cached_indices_time
        indices = get_cached_indices()
        sentiment = get_market_sentiment()
        if indices:
            idx_ts = get_cached_indices_time()
            ts_suffix = f"（数据时间 {idx_ts}）" if idx_ts else ""
            idx_lines = [f"\nMARKET INDICES (LIVE){ts_suffix}:"]
            for s, n in [("sh000001", "上证"), ("sz399001", "深证"), ("sz399006", "创业板"), ("sh000688", "科创50"), ("sh000300", "沪深300")]:
                d = indices.get(s, {})
                if d:
                    idx_lines.append(f"  {n}: {d.get('price', 'N/A')} ({d.get('change_pct', 0):+.2f}%)")
            idx_lines.append(f"MARKET SENTIMENT: {sentiment.get('sentiment', 'unknown')} (score: {sentiment.get('score', 0)})")
            idx_lines.append(f"INDICES_TREND: {sentiment.get('detail', '')}")
            user_prompt += "\n" + "\n".join(idx_lines)
    except Exception:
        logger.warning("上下文注入失败: 大盘指数数据注入异常", exc_info=True)

    # 注入活跃订单（供 LLM 识别撤单目标）
    try:
        from ..services.order_engine import get_active_orders as get_active_orders_oe
        active_orders = get_active_orders_oe(user_id)
        if active_orders:
            order_lines = ["\nACTIVE ORDERS (PENDING/CANCELLABLE - use cancel_order_id to cancel):"]
            for o in active_orders:
                oid = o.get("order_id", "?")
                osym = o.get("symbol", "?")
                oname = o.get("name", "")
                oside = "买入" if o.get("side") == "BUY" else "卖出"
                oqty = o.get("quantity", 0)
                oprice = o.get("price", 0)
                ofilled = o.get("filled_qty", 0)
                ostat = o.get("status", "")
                otype = "限价" if o.get("order_type") == "LIMIT" else "市价"
                order_lines.append(f"  #{oid}: [{otype}] {oside} {oname or osym} {oqty}股@{oprice} (已成交{ofilled}股) [{ostat}]")
            user_prompt += "\n" + "\n".join(order_lines)
    except Exception:
        logger.warning("上下文注入失败: 活跃订单数据注入异常", exc_info=True)

    # 注入最近成交记录（含卖出已实现收益）
    try:
        from ..services.db import get_all_orders_db
        recent_orders = await get_all_orders_db(user_id, status_filter=["FILLED"], limit=15)
        if recent_orders:
            recent_lines = ["\nRECENT FILLED TRADES (最近成交记录，含卖出已实现收益):"]
            for o in recent_orders:
                oid = o.get("order_id") or o.get("id", "?")
                osym = o.get("symbol", "?")
                oname = o.get("name", "")
                oside = "买入" if o.get("side") == "BUY" else "卖出"
                oqty = o.get("quantity", 0)
                oprice = o.get("price", 0)
                orealized = o.get("realized_pnl")
                created = o.get("created_at", "")[:16]
                pnl_str = f" 已实现收益{orealized:+.2f}元" if oside == "卖出" and orealized is not None else ""
                recent_lines.append(f"  [{created}] {oside} {oname or osym} {oqty}股@{oprice}{pnl_str}")
            user_prompt += "\n" + "\n".join(recent_lines)
    except Exception:
        logger.warning("上下文注入失败: 历史订单数据注入异常", exc_info=True)

    return user_prompt


def _build_chief_strategist_log(user_input: str, intent: str, intent_cn: str, name: str,
                                symbol: str, in_watchlist: bool, trade_side: str,
                                trade_quantity: int, watchlist: list,
                                needed_agents: list, reasoning: str) -> str:
    """构建首席策略官决策过程日志"""
    lines = [f"收到指令：「{user_input}」", ""]
    lines.append(f"意图识别：{intent_cn}")
    if name and symbol:
        lines.append(f"目标股票：{name}（{symbol}）{'[在自选股]' if in_watchlist else '[不在自选股]'}")
    if trade_side:
        lines.append(f"交易方向：{'买入' if trade_side == 'BUY' else '卖出'}")
    if trade_quantity:
        lines.append(f"交易数量：{trade_quantity}股")
    if reasoning:
        lines.append(f"决策推理：{reasoning}")
    lines.append("")
    if needed_agents:
        agent_names = [AGENT_CN_MAP.get(a, a) for a in needed_agents]
        lines.append(f"调度 Agent 团队：{' → '.join(agent_names)}")
        lines.append(f"共需调用 {len(needed_agents)} 个 Agent，正在依次执行...")
    else:
        lines.append("无需调度其他 Agent，直接回复用户")
    return "\n".join(lines)


def _extract_symbol(text: str, watchlist: list) -> tuple:
    """本地规则提取股票代码（返回纯6位代码），优先使用 stock_lookup 全量数据"""
    # 空输入或太短的输入不提取股票代码
    if not text or not text.strip() or len(text.strip()) < 2:
        return None, None
    # 1. 尝试用 stock_lookup 全量数据解析（双方向）
    try:
        from ..services.stock_lookup import resolve
        # 1a: 全文精确解析
        result = resolve(text)
        if result:
            return result["code"], result["name"]
        # 1b: 递归去掉常见查询词缀后重试（"茅台走势分析" → "茅台"）
        stripped = text
        _suffixes = ["多少钱", "价格", "多少", "走势分析", "分析", "走势", "怎么样", "情况", "如何",
                     "基本面", "技术面", "消息面", "跌了多少", "涨了多少"]
        _prefixes = ["买入", "卖出", "买", "卖", "怎么", "分析", "移除", "帮我看下", "查看", "查"]
        changed = True
        while changed:
            changed = False
            for suffix in _suffixes:
                if stripped.endswith(suffix):
                    stripped = stripped[:len(stripped)-len(suffix)].strip()
                    changed = True
                    break
            for prefix in _prefixes:
                if stripped.startswith(prefix):
                    stripped = stripped[len(prefix):].strip()
                    changed = True
                    break
            # 去掉尾部数字+单位（如"100股"、"100手"）
            changed2 = re.sub(r'[\d,，]+[股手张]?\s*$', '', stripped)
            if changed2 != stripped and changed2.strip():
                stripped = changed2.strip()
                changed = True
        if stripped != text:
            result = resolve(stripped)
            if result:
                return result["code"], result["name"]
        # 1c: 扫描已知股票名称/简称是否出现在查询文本中（仅 ≥3 字符子串）
        # 避免 2 字符短串误匹配（如"今天"→"今天国际"、"创业"→"西部创业"）
        from ..services.stock_lookup import _get as _get_lookup
        ni = _get_lookup().get("name_index", {})
        ci = _get_lookup().get("code_index", {})
        found_candidates = []
        seen = set()
        for stock_name, codes in ni.items():
            if not codes:
                continue
            code = codes[0]
            # 完整名称匹配（如"贵州茅台"出现在"查看贵州茅台价格"中）
            if len(stock_name) >= 2 and stock_name in text:
                found_candidates.append((len(stock_name), code, stock_name, 3))
                continue
            # 子串匹配（≥3字符，如"茅台"匹配"帮我看下茅台跌了多少"）
            nlen = len(stock_name)
            for win_sz in range(min(nlen, 4), 2, -1):  # win 3-4 chars
                for i in range(nlen - win_sz + 1):
                    snippet = stock_name[i:i+win_sz]
                    if snippet in seen:
                        continue
                    seen.add(snippet)
                    if snippet in text:
                        found_candidates.append((win_sz, code, stock_name, 2))
        if found_candidates:
            found_candidates.sort(key=lambda x: (-x[3], -x[0]))
            best = found_candidates[0]
            return best[1], best[2]
    except Exception:
        pass

    # 2. 代码正则匹配
    code_pattern = re.compile(r'(?:sh|sz|bj)?(\d{6})')
    match = code_pattern.search(text)
    if match:
        raw = match.group(0).lower()
        if raw.startswith(("sh", "sz", "bj")):
            symbol = raw[2:]
        else:
            symbol = raw
        try:
            from ..services.stock_lookup import get_name
            name = get_name(symbol)
            if name:
                return symbol, name
        except Exception:
            pass
        return symbol, raw

    # 3. 自选股匹配
    for item in watchlist:
        if item.get("name", "") in text:
            return item["symbol"], item["name"]

    return None, None


# Direct Agent Name Resolution - Deterministic local matching
# This bypasses LLM hallucination risk for critical routing.

_AGENT_NAME_PATTERNS = [
    # (regex, agent_key) — supports both old and new names for compatibility
    (r"量化分析|量化研究员|量化研究|量化|quant", "quant_researcher"),
    (r"首席策略官|首席策略|策略官|策略路由|chief", "chief_strategist"),
    (r"市场动态感知官|市场感知|市场情报|市场动态|情报官|intel", "market_intelligence"),
    (r"交易执行员|交易执行|交易员|交易官|trade[\s_]?executor", "trade_executor"),
    (r"风控持仓监控官|风控官|持仓风控|持仓监控|风控|portfolio", "portfolio_monitor"),
    (r"@量化|@quant", "quant_researcher"),
    (r"@市场|@情报|@intel", "market_intelligence"),
    (r"@交易|@trade", "trade_executor"),
    (r"@风控|@持仓|@portfolio", "portfolio_monitor"),
    (r"@首席|@chief", "chief_strategist"),
    (r"@助手", "chief_strategist"),
    # 召唤/交互模式：用户想与某个 Agent 对话
    (r"召唤.*(出来|说|聊|讲话|打个招呼)", "chief_strategist"),
    (r"再?(来|叫|召唤|喊).*一个", "chief_strategist"),
    (r"出来.*(说|聊|讲话|打个招呼|跟我)", "chief_strategist"),
]


def _detect_direct_agent(text: str) -> tuple[str, str]:
    """Detect if user is directly addressing a specific agent.
    
    Uses deterministic regex matching (NOT LLM) for reliability.
    Returns (primary_agent, comma_separated_all_agents) or ("", "").
    When multiple agents matched, primary is the first match, and all are returned.
    """
    # Identity/self-reference patterns (question about the speaker being a specific agent)
    identity_agent = _detect_identity_question(text)
    if identity_agent:
        return identity_agent, identity_agent
    
    matched = []
    for pattern, agent_key in _AGENT_NAME_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            if agent_key not in matched:
                matched.append(agent_key)
    
    if not matched:
        return "", ""
    
    primary = matched[0]
    all_agents = ",".join(matched)
    return primary, all_agents


def _detect_identity_question(text: str) -> str:
    """Detect if user is questioning the identity of an agent.
    
    Patterns: "你不是XX吧" "你是XX吗" "你真的是XX吗" "你真不是XX?"
    """
    identity_patterns = [
        r"你不?是(量化|首席|策略|市场|情报|交易|风控|持仓|quant|chief|trade|intel|portfolio)",
        r"你是(量化|首席|策略|市场|情报|交易|风控|持仓|quant|chief|trade|intel|portfolio)[吗吧]",
        r"你真(的)?是(量化|首席|策略|市场|情报|交易|风控|持仓|quant|chief|trade|intel|portfolio)",
        r"你(真)?不是(量化|首席|策略|市场|情报|交易|风控|持仓|quant|chief|trade|intel|portfolio)",
    ]
    for ptn in identity_patterns:
        match = re.search(ptn, text, re.IGNORECASE)
        if match:
            # Extract all non-None groups and find the keyword
            groups = [g for g in match.groups() if g]
            for g in groups:
                agent = _keyword_to_agent(g)
                if agent:
                    return agent
    return ""


def _keyword_to_agent(keyword: str) -> str:
    """Map a Chinese/English keyword to agent key."""
    kw = keyword.lower()
    if kw in ("量化", "quant"):
        return "quant_researcher"
    if kw in ("首席", "策略", "chief", "助手"):
        return "chief_strategist"
    if kw in ("市场", "情报", "intel", "market"):
        return "market_intelligence"
    if kw in ("交易", "trade"):
        return "trade_executor"
    if kw in ("风控", "持仓", "portfolio"):
        return "portfolio_monitor"
    return ""
