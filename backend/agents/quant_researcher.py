"""量化研究员 Agent - 技术面分析"""
import json as json_mod
import logging
from datetime import datetime

from ..services.agent_memory import (
    compute_query_hash,
    get_agent_memory,
    save_agent_memory,
)
from ..services.data_source_manager import data_source_manager
from ..services.llm import choose_client
from ..services.market_tool import get_stock_realtime
from ..services.quant_signals import compute_extended_indicators, compute_quant_score
from ..services.technical_analysis import technical_analyzer
from ..services.trading_time import TradingTimeChecker
from .prompts import AGENT_PROFILES, QUANT_RESEARCHER_SYSTEM
from .state import AgentState
from .utils import maybe_attach_followup

logger = logging.getLogger(__name__)


async def quant_researcher_node(state: AgentState) -> AgentState:
    """量化研究员节点：获取K线数据 + 技术指标计算 + LLM分析"""
    symbol = state.get("active_symbol")
    name = state.get("active_name", "")

    # 批量分析自选股列表：逐只做确定性量化扫描（不逐只调 LLM，保证速度与稳定）
    if state.get("analyze_watchlist") and state.get("watchlist"):
        return await _analyze_watchlist_batch(state)

    if not symbol:
        state["technical_analysis"] = None
        return state

    # 获取K线数据
    kline_data = await data_source_manager.get_kline(symbol, "day", 250)
    state["kline_data"] = kline_data

    # 同步获取实时行情，供所有下游 Agent/响应生成器使用
    realtime = await get_stock_realtime(symbol)
    if realtime:
        state["market_data"] = {symbol: realtime}

    # 技术分析
    analysis = None
    if kline_data and len(kline_data) >= 20:
        analysis = technical_analyzer.analyze(kline_data)
        # 扩展专业指标 + 多信号加权评分（提升预测稳定性）
        try:
            ext = compute_extended_indicators(kline_data)
            if ext:
                analysis["extended"] = ext
                analysis["quant_score"] = compute_quant_score(analysis, ext)
        except Exception:
            logger.warning("扩展量化指标计算失败", exc_info=True)
        state["technical_analysis"] = analysis
    else:
        state["technical_analysis"] = None

    # 基本面估值（PE/PB/换手率/成交额），供策略与交易执行综合判断
    if realtime:
        state["fundamental_analysis"] = _assess_fundamentals(realtime)

    # LLM 分析解读
    if analysis:
        try:
            user_input = state.get("user_input", "")
            user_id = state.get("user_id", "default")
            agent_key = "quant_researcher"
            query_hash = compute_query_hash(user_input, agent_key, symbol)
            is_trading = TradingTimeChecker.is_trading_time()

            # 盘中始终调用 LLM 获取最新分析，盘后/非交易时段优先使用缓存
            cached = None
            if not is_trading:
                cached = await get_agent_memory(user_id, agent_key, symbol, query_hash, query=user_input)

            if cached and not is_trading:
                try:
                    llm_result = json_mod.loads(cached)
                    state["quant_assessment"] = llm_result
                except Exception:
                    cached = None

            if cached is None or is_trading:
                # A4：优先多轮工具编排，解析失败回退到预取数据单次调用
                llm_result = await _quant_llm_with_tools(state, symbol, name)
                if llm_result.get("parse_error"):
                    llm_result = await _quant_llm_legacy(state, analysis, name or symbol)
                if llm_result.get("parse_error"):
                    raise ValueError("LLM returned non-JSON")
                state["quant_assessment"] = llm_result

                # 保存到 agent_memory
                await save_agent_memory(
                    user_id, agent_key, symbol,
                    query_hash, json_mod.dumps(llm_result, ensure_ascii=False, default=str),
                    query=user_input
                )
        except Exception:
            state["quant_assessment"] = None

    # Emit agent_log
    analysis = state.get("technical_analysis") or {}
    quant_assessment = state.get("quant_assessment")

    if analysis:
        trend = analysis.get("trend", "N/A")
        rsi = analysis.get("rsi", "N/A")
        macd_sig = analysis.get("macd_signal", "N/A")
        ma = analysis.get("ma", {})
        vol = analysis.get("volatility", "N/A")
        latest = analysis.get("latest_price", "N/A")

        # 简洁人格化输出：3-4行关键信息
        trend_emoji = "↗" if trend == "bullish" else "↘" if trend == "bearish" else "→"
        trend_cn = "多头" if trend == "bullish" else "空头" if trend == "bearish" else "震荡"
        
        lines = [f"瞄了一眼{name or symbol}：现价{latest}，趋势{trend_emoji}{trend_cn}，RSI {rsi}，MACD {macd_sig}"]
        
        # 关键均线信号
        ma5, ma20, ma60 = ma.get('ma5'), ma.get('ma20'), ma.get('ma60')
        if ma5 and ma20:
            if ma5 > ma20:
                lines.append(f"MA5({ma5})在MA20({ma20})上方，短期偏强")
            else:
                lines.append(f"MA5({ma5})在MA20({ma20})下方，短期偏弱")
        if ma60 and latest:
            lines.append(f"距MA60({ma60})还有段距离，中期均线还是{'阻力' if latest < ma60 else '支撑'}")

        # 多信号集成评分 + 基本面估值
        qs = analysis.get("quant_score") or {}
        if qs:
            lines.append(f"多信号评分{qs.get('score')}/100（{qs.get('label')}），波动率{vol}%")
        fa = state.get("fundamental_analysis") or {}
        if fa:
            lines.append(f"基本面：PE {fa.get('pe')}，PB {fa.get('pb')}，估值{fa.get('valuation')}")
        
        # 量化评估摘要
        if quant_assessment and not quant_assessment.get("parse_error"):
            sa = quant_assessment.get("summary", "")
            strength = quant_assessment.get("strength_rating", "")
            if sa:
                lines.append(f"我的判断：{sa}（强度{strength}/10）")

        agent_log_content = "\n".join(lines)
    else:
        agent_log_content = "数据不够，技术面没法定性，得等K线攒够再说。"

    agent_log = {
        "agent": "quant_researcher",
        "emoji": AGENT_PROFILES["quant_researcher"]["emoji"],
        "name_cn": AGENT_PROFILES["quant_researcher"]["name_cn"],
        "color": AGENT_PROFILES["quant_researcher"]["color"],
        "content": agent_log_content,
        "timestamp": datetime.now().astimezone().isoformat(),
    }
    agent_log = maybe_attach_followup(agent_log, "quant_researcher")
    state.setdefault("agent_logs", []).append(agent_log)

    return state


async def _analyze_watchlist_batch(state: AgentState) -> AgentState:
    """批量扫描自选股：对每只做确定性技术指标计算，生成简洁汇总日志。

    不逐只调用 LLM（避免慢 + 限流），用 technical_analyzer + quant_score 等
    确定性信号给出稳定结论；第一只的分析结果挂载到 technical_analysis 供下游复用。
    """
    watchlist = state.get("watchlist", [])
    logs = []
    first_analysis = None

    for w in watchlist[:10]:
        symbol = w.get("symbol")
        name = w.get("name") or symbol
        if not symbol:
            continue
        try:
            kline = await data_source_manager.get_kline(symbol, "day", 120)
            realtime = await get_stock_realtime(symbol)
            if not kline or len(kline) < 20:
                logs.append(f"{name}（{symbol}）：K线数据不足，暂无法定性")
                continue

            analysis = technical_analyzer.analyze(kline)
            ext = compute_extended_indicators(kline)
            if ext:
                analysis["extended"] = ext
                analysis["quant_score"] = compute_quant_score(analysis, ext)

            trend = analysis.get("trend", "N/A")
            trend_cn = "多头" if trend == "bullish" else "空头" if trend == "bearish" else "震荡"
            qs = analysis.get("quant_score") or {}
            latest = analysis.get("latest_price", "N/A")
            rsi = analysis.get("rsi", "N/A")

            line = f"{name}（{symbol}）：现价{latest}，趋势{trend_cn}，RSI {rsi}"
            if qs:
                line += f"，多信号评分{qs.get('score')}/100（{qs.get('label')}）"
            if realtime:
                fa = _assess_fundamentals(realtime)
                if fa:
                    line += f"，PE {fa.get('pe')}（{fa.get('valuation')}）"
            logs.append(line)

            if first_analysis is None:
                first_analysis = analysis
        except Exception as e:
            logger.warning("自选股批量扫描失败 %s: %s", symbol, e)
            logs.append(f"{name}（{symbol}）：分析失败")

    if not logs:
        logs.append("自选股列表为空，无法分析")

    state["technical_analysis"] = first_analysis

    agent_log = {
        "agent": "quant_researcher",
        "emoji": AGENT_PROFILES["quant_researcher"]["emoji"],
        "name_cn": AGENT_PROFILES["quant_researcher"]["name_cn"],
        "color": AGENT_PROFILES["quant_researcher"]["color"],
        "content": "自选股扫描：\n" + "\n".join(logs),
        "timestamp": datetime.now().astimezone().isoformat(),
    }
    agent_log = maybe_attach_followup(agent_log, "quant_researcher")
    state.setdefault("agent_logs", []).append(agent_log)
    return state


def _assess_fundamentals(realtime: dict) -> dict:
    """从实时行情提取基本面估值信号（PE/PB/换手率/成交额）。"""
    pe = float(realtime.get("pe", 0) or 0)
    pb = float(realtime.get("pb", 0) or 0)
    turnover = float(realtime.get("turnover", 0) or 0)
    amount = float(realtime.get("amount", 0) or 0)

    if pe <= 0:
        valuation = "亏损（PE无参考意义）"
    elif pe < 15:
        valuation = "低估"
    elif pe < 30:
        valuation = "合理"
    elif pe < 50:
        valuation = "偏高"
    else:
        valuation = "高估"

    if pb > 0 and pb < 1:
        valuation += "（破净）"

    return {
        "pe": round(pe, 2),
        "pb": round(pb, 2),
        "turnover": round(turnover, 2),
        "amount": round(amount / 1e8, 2),  # 亿元
        "valuation": valuation,
    }


def _build_tech_summary(analysis: dict, label: str) -> str:
    """Build a text summary of technical analysis for LLM input."""
    lines = [
        f"Stock: {label}",
        f"Latest Price: {analysis.get('latest_price')}",
        f"Trend: {analysis.get('trend')}",
    ]
    ma = analysis.get("ma", {})
    lines.append(
        f"MA: MA5={ma.get('ma5')}, MA10={ma.get('ma10')}, "
        f"MA20={ma.get('ma20')}, MA60={ma.get('ma60')}"
    )
    lines.append(f"RSI: {analysis.get('rsi')}")
    lines.append(f"MACD Signal: {analysis.get('macd_signal')}")
    bb = analysis.get("bollinger", {})
    lines.append(
        f"Bollinger Bands: upper={bb.get('upper')}, middle={bb.get('middle')}, "
        f"lower={bb.get('lower')}"
    )
    lines.append(f"Volatility: {analysis.get('volatility')}%")
    lines.append(f"Support: {analysis.get('support')}, Resistance: {analysis.get('resistance')}")
    # 扩展指标 + 集成评分
    ext = analysis.get("extended") or {}
    if ext:
        lines.append(
            f"Extended: ATR={ext.get('atr')}({ext.get('atr_pct')}%), "
            f"KDJ={ext.get('kdj')}, ROC={ext.get('roc')}, "
            f"Williams%R={ext.get('williams_r')}, CCI={ext.get('cci')}, "
            f"ADX={ext.get('adx')}, OBV={ext.get('obv_trend')}, VolTrend={ext.get('volume_trend')}, "
            f"MFI={ext.get('mfi')}, BIAS={ext.get('bias')}%, PSY={ext.get('psy')}, Regime={ext.get('regime')}"
        )
    qs = analysis.get("quant_score")
    if qs:
        lines.append(f"Quant Score: {qs.get('score')}/100 ({qs.get('label')})")
    signals = analysis.get("signals", [])
    if signals:
        lines.append("Technical Signals:")
        for s in signals:
            lines.append(
                f"- [{s.get('type', '')}] {s.get('signal', '')} "
                f"(strength: {s.get('strength', '')})"
            )
    return "\n".join(lines)


async def _quant_llm_with_tools(state: AgentState, symbol: str, name: str) -> dict:
    """A4：多轮工具编排，LLM 自主决定查询行情/K线。失败返回 parse_error 标记。"""
    from .tool_agent import parse_agent_json, run_tool_agent
    from .tools import TOOLS_BY_AGENT

    user_input = state.get("user_input", "")
    history_summary = state.get("history_summary", "")
    strategy = state.get("strategy_direction", "")
    current_time = state.get("current_time", "")

    ctx_lines = [f"User asked: {user_input}", f"Target stock: {symbol} ({name or ''})"]
    if history_summary:
        ctx_lines.append(f"Conversation context: {history_summary}")
    if strategy:
        ctx_lines.append(f"Orchestrator reasoning: {strategy}")
    if current_time:
        ctx_lines.append(f"Current system time: {current_time}")
    user_prompt = "\n".join(ctx_lines)

    try:
        content, trace = await run_tool_agent(
            choose_client(True),
            QUANT_RESEARCHER_SYSTEM,
            user_prompt,
            TOOLS_BY_AGENT["quant_researcher"],
        )
        if trace:
            logger.info("quant tool trace: %s", [t["tool"] for t in trace])
        return parse_agent_json(content)
    except Exception as e:
        logger.warning("quant tool loop exception: %s", e)
        return {"raw": str(e), "parse_error": True}


async def _quant_llm_legacy(state: AgentState, analysis: dict, label: str) -> dict:
    """A4 降级：预取技术面数据 + 单次 LLM 调用（原逻辑）"""
    user_input = state.get("user_input", "")
    history_summary = state.get("history_summary", "")
    strategy = state.get("strategy_direction", "")
    current_time = state.get("current_time", "")

    tech_summary = _build_tech_summary(analysis, label)
    time_prefix = f"Current system time: {current_time}\n\n" if current_time else ""
    ctx_lines = [f"User asked: {user_input}"]
    if history_summary:
        ctx_lines.append(f"Conversation context: {history_summary}")
    if strategy:
        ctx_lines.append(f"Orchestrator reasoning: {strategy}")
    ctx_prefix = "\n".join(ctx_lines) + "\n\n"
    messages = [
        {"role": "system", "content": QUANT_RESEARCHER_SYSTEM},
        {"role": "user", "content": ctx_prefix + time_prefix + tech_summary},
    ]
    return await choose_client(True).chat_json(messages, temperature=0.1, max_tokens=1024)
