"""响应生成器 - 基于 DeepSeek LLM 的专业中文回复"""

from ..services.technical_analysis import technical_analyzer
from .state import AgentState


async def response_generator_node(state: AgentState) -> AgentState:
    """响应生成器节点：准备上下文（实际LLM调用在SSE端点流式执行）

    不 emit agent_log —— 真正的内容在 SSE 流中由 LLM 生成后推送。
    """
    return state


def _build_context(state: AgentState) -> str:
    """构建传给 LLM 的上下文"""
    intent = state.get("intent", "chat")
    current_time = state.get("current_time", "")
    detail_level = state.get("detail_level", "auto")
    history_summary = state.get("history_summary", "")

    # 今日交易状态（是否休市），供回答"现在几点"等时间问题时顺带说明
    market_status = ""
    try:
        from ..services.trading_time import TradingTimeChecker

        market_status = TradingTimeChecker.market_status_text()
    except Exception:
        market_status = ""

    # chat/watchlist 意图也注入历史上下文，便于回答"我之前说了什么"等回忆类问题
    if intent in ("chat", "watchlist"):
        ctx = f"INTENT: {intent}\nUSER_MESSAGE: {state.get('user_input', '')}\nCURRENT_TIME: {current_time or 'unknown'}\nDETAIL_LEVEL: {detail_level}\n"
        if market_status:
            ctx += f"MARKET_STATUS: {market_status}\n"
        if history_summary:
            ctx += f"\nCONVERSATION_HISTORY:\n{history_summary}\n"
        ctx += "\nGenerate a brief, friendly Chinese response. If the user asks about the current time/date, include the MARKET_STATUS (today is 休市 or 交易日). If the user asks about what was said earlier (e.g. '我之前说了什么'), answer based on CONVERSATION_HISTORY above. Otherwise respond naturally. No analysis, no report, no charts."
        return ctx

    strategy = state.get("strategy_direction", "")

    parts = [f"用户消息: {state.get('user_input', '')}\n"]
    if history_summary:
        parts.append(f"对话历史摘要: {history_summary}")
    if strategy:
        parts.append(f"策略分析: {strategy}")
    # Do NOT expose intent labels - the LLM will infer the task from context
    if current_time:
        parts.append(f"当前系统时间: {current_time}")
    if market_status:
        parts.append(f"今日交易状态: {market_status}")
    if detail_level and detail_level != "auto":
        parts.append(
            f"用户要求回复详细程度: {detail_level} ({'详细分析' if detail_level == 'detailed' else '简短回答'})"
        )

    symbol = state.get("active_symbol")
    name = state.get("active_name")
    watchlist = state.get("watchlist", [])

    if name and symbol:
        parts.append(f"目标股票: {name}({symbol})")
    if watchlist:
        wl_names = ", ".join(w["name"] + "(" + w["symbol"] + ")" for w in watchlist)
        parts.append(f"用户自选股: {wl_names}")

    # 实时行情
    market_data = state.get("market_data", {})
    for v in market_data.values():
        parts.append(
            f"\n实时行情 {v.get('name', '')}: 价格{v.get('price')}, 涨跌{v.get('change_pct')}%, 昨收{v.get('prev_close')}, 今开{v.get('open')}, 最高{v.get('high')}, 最低{v.get('low')}, 成交量{v.get('volume')}手, 换手率{v.get('turnover')}%"
        )

    # 技术分析
    tech = state.get("technical_analysis")
    if tech:
        parts.append("\n技术分析数据:")
        parts.append(f"- 趋势: {tech.get('trend')}")
        parts.append(f"- 最新价: {tech.get('latest_price')}")
        ma = tech.get("ma") or {}
        bb = tech.get("bollinger") or {}
        parts.append(
            f"- 均线: MA5={ma.get('ma5')}, MA10={ma.get('ma10')}, MA20={ma.get('ma20')}, MA60={ma.get('ma60')}"
        )
        parts.append(f"- RSI: {tech.get('rsi')}")
        parts.append(f"- MACD信号: {tech.get('macd_signal')}")
        parts.append(
            f"- 布林带: 上轨{bb.get('upper')}, 中轨{bb.get('middle')}, 下轨{bb.get('lower')}"
        )
        parts.append(f"- 波动率: {tech.get('volatility')}%")
        signals = tech.get("signals", [])
        if signals:
            for s in signals:
                parts.append(
                    f"  * [{s.get('type')}] {s.get('signal')} (强度:{s.get('strength')})"
                )
        parts.append(f"- 支撑位: {tech.get('support')}")
        parts.append(f"- 阻力位: {tech.get('resistance')}")

    # 量化分析结果
    quant_assessment = state.get("quant_assessment")
    if quant_assessment:
        parts.append("\n量化评估:")
        parts.append(f"- 综合判断: {quant_assessment.get('summary', '')}")
        parts.append(f"- 趋势评估: {quant_assessment.get('trend_assessment', '')}")
        parts.append(f"- 强度评分: {quant_assessment.get('strength_rating', '')}/10")
        parts.append(f"- 波动评估: {quant_assessment.get('volatility_assessment', '')}")
        key_signals = quant_assessment.get("key_signals", [])
        if key_signals:
            parts.append(f"- 关键信号: {'; '.join(key_signals)}")
        risk_flags = quant_assessment.get("risk_flags", [])
        if risk_flags:
            parts.append(f"- 技术风险: {'; '.join(risk_flags)}")

    # 市场情报分析
    intel_assessment = state.get("intelligence_assessment")
    if intel_assessment:
        parts.append("\n市场评估:")
        parts.append(
            f"- 情绪: {intel_assessment.get('sentiment_label', '')} ({intel_assessment.get('sentiment_score', 0)})"
        )
        parts.append(
            f"- 影响: {intel_assessment.get('impact_direction', '')}/{intel_assessment.get('impact_strength', '')}"
        )
        parts.append(f"- 影响摘要: {intel_assessment.get('impact_summary', '')}")
        key_factors = intel_assessment.get("key_factors", [])
        if key_factors:
            parts.append(f"- 关键因子: {'; '.join(key_factors)}")
        risk_alerts = intel_assessment.get("risk_alerts", [])
        if risk_alerts:
            parts.append(f"- 风险预警: {'; '.join(risk_alerts)}")

    # 市场动态（原始数据）
    intel = state.get("market_intelligence")
    if intel:
        if intel.get("type") == "market_overview":
            parts.append("\n市场整体概况:")
            status = intel.get("status", {})
            parts.append(f"- 交易状态: {status.get('detail', '未知')}")
            smry = intel.get("summary", {})
            parts.append(
                f"- 指数涨跌: 涨{smry.get('up_count', 0)} / 跌{smry.get('down_count', 0)} / 平{smry.get('flat_count', 0)}"
            )
            sentiment = intel.get("sentiment", {})
            parts.append(
                f"- 市场情绪: {sentiment.get('sentiment', 'unknown')} (评分 {_as_float(sentiment.get('score')):+.2f})"
            )
            indices = intel.get("indices", {})
            if indices:
                parts.append("- 主要指数:")
                for sym, d in indices.items():
                    parts.append(
                        f"  * {d.get('name', sym)}: {d.get('price')} ({_as_float(d.get('change_pct')):+.2f}%)"
                    )
        else:
            parts.append("\n市场动态情报:")
            parts.append(
                f"- 情绪评分: {intel.get('sentiment_label')} ({intel.get('sentiment_score')})"
            )
            impact = intel.get("impact", {})
            parts.append(
                f"- 影响评估: {impact.get('direction')}({impact.get('strength')}) - {impact.get('reason')}"
            )
            news = intel.get("news", [])
            if news:
                parts.append("- 最新新闻:")
                for n in news[:5]:
                    parts.append(
                        f"  * [{n.get('source')}] {n.get('title')} (可信度:{n.get('credibility', 0)})"
                    )
            risk_alerts = intel.get("risk_alerts", [])
            if risk_alerts:
                parts.append("- 风险预警:")
                for a in risk_alerts:
                    parts.append(f"  * [{a.get('level')}] {a.get('title')}")

    # 持仓
    portfolio = state.get("portfolio_summary")
    if portfolio:
        parts.append("\n持仓概览:")
        parts.append(f"- 可用资金: {portfolio.get('balance', 0):,.2f}")
        parts.append(f"- 持仓市值: {portfolio.get('total_market_value', 0):,.2f}")
        parts.append(f"- 总资产: {portfolio.get('total_assets', 0):,.2f}")
        parts.append(
            f"- 累计盈亏: {portfolio.get('total_pnl', 0):+,.2f} ({portfolio.get('total_pnl_pct', 0):+.2f}%)"
        )
        positions = portfolio.get("positions", [])
        if positions:
            parts.append("- 持仓明细:")
            for p in positions:
                t1 = "[T+1限制]" if p.get("t1_restricted") else ""
                parts.append(
                    f"  * {p['name']} {t1}: 持有{p['quantity']}股, 成本{p['avg_cost']}, 现价{p['current_price']}, 盈亏{p['pnl']:+,.2f}"
                )

    # 持仓评估
    portfolio_assessment = state.get("portfolio_assessment")
    if portfolio_assessment:
        parts.append("\n持仓评估:")
        parts.append(f"- 健康度: {portfolio_assessment.get('portfolio_health', '')}")
        parts.append(
            f"- 集中度风险: {portfolio_assessment.get('concentration_risk', '')}"
        )
        parts.append(f"- 回撤状态: {portfolio_assessment.get('drawdown_status', '')}")
        recs = portfolio_assessment.get("recommendations_chinese", [])
        if recs:
            parts.append(f"- 建议: {'; '.join(recs)}")

    # 交易计划
    trade_plan = state.get("trade_plan")
    if trade_plan:
        parts.append("\n交易计划:")
        parts.append(f"- 方向: {trade_plan.get('side')}")
        parts.append(f"- 数量: {trade_plan.get('quantity')}股")
        parts.append(f"- 当前价: {trade_plan.get('current_price')}")
        parts.append(
            f"- 交易时间: {'是' if trade_plan.get('is_trading_time') else '否（非交易时间）'}"
        )
        parts.append(f"- 风险等级: {trade_plan.get('risk_level')}")
        warnings = trade_plan.get("warnings", [])
        if warnings:
            parts.append("- 风险提示:")
            for w in warnings:
                parts.append(f"  * [{w.get('source')}] {w.get('message')}")
        recs = trade_plan.get("recommendations", [])
        if recs:
            parts.append("- 建议:")
            for r in recs:
                parts.append(f"  * [{r.get('source')}] {r.get('message')}")

    # 交易执行评估
    executor_assessment = state.get("executor_assessment")
    if executor_assessment:
        parts.append("\n交易评估:")
        parts.append(f"- 建议: {executor_assessment.get('trade_recommendation', '')}")
        parts.append(f"- 风险: {executor_assessment.get('risk_level', '')}")
        parts.append(
            f"- 风险评估: {executor_assessment.get('risk_assessment_chinese', '')}"
        )

    # 交易结果
    order_result = state.get("order_result")
    if order_result:
        parts.append(
            f"\n交易结果: {'成功' if order_result.get('success') else '失败'} - {order_result.get('message')}"
        )

    # 根据意图与是否要求报告添加指令（默认对话式，仅显式要求时才生成报告）
    needs_report = state.get("needs_report", False)
    if intent == "analyze":
        if needs_report:
            parts.append(
                "\n请根据以上技术分析和市场情报，生成一份专业的股票分析报告。要包含技术面、消息面、综合研判。"
            )
        elif detail_level == "brief":
            parts.append("\n请用一两句话简洁概括该股票的核心要点。")
        else:
            parts.append(
                "\n请用对话式语言简要总结该股票的技术面和消息面要点，80-200字即可。"
            )
    elif intent == "trade":
        if needs_report:
            parts.append(
                "\n请根据交易计划和风险评估，给用户一个清晰的交易确认回复。要列出风险提示。"
            )
        else:
            parts.append(
                "\n请简短确认交易计划的关键信息（方向/数量/价格/风险），对话式即可。"
            )
    elif intent == "portfolio":
        if needs_report:
            parts.append("\n请总结用户的持仓情况和盈亏表现。")
        else:
            parts.append("\n请用对话式语言简要总结持仓和盈亏概况。")
    elif intent == "watchlist":
        parts.append("\n请回复用户关于自选股管理的操作结果。")
    elif intent == "query":
        parts.append("\n请简洁地回复用户查询的行情信息。")
    elif intent == "market":
        if needs_report:
            parts.append(
                "\n请根据以上大盘指数和市场情绪数据，生成一份简洁的市场概览。包含主要指数涨跌、市场情绪、交易状态提醒。"
            )
        else:
            parts.append(
                "\n请用对话式语言简要说明当前大盘指数和市场情绪，一两句话即可。"
            )
    elif intent == "chat":
        if not symbol and not watchlist:
            parts.append(
                "\n用户是首次使用，请用友好的语气介绍系统功能，引导用户添加自选股（最多3只）。系统支持分析、交易、持仓查看等功能。"
            )
        elif not symbol:
            parts.append(
                "\n用户没有指定股票，请友好地引导用户说出想了解的股票名称或代码。"
            )
        else:
            parts.append("\n请友好回应用户的问题。")

    # D3：低置信度主动求助（量化/情报分析不确定性高时提示人工复核）
    low_conf = _low_confidence_note(state)
    if low_conf:
        parts.append(low_conf)

    return "\n".join(parts)


_LOW_CONFIDENCE_NOTE = (
    "\n注意：以下分析存在较大不确定性，请在回复中明确提示用户"
    "「以上分析仅供参考，建议结合人工复核，不构成投资建议」。"
)


def _as_float(value, default: float = 0.0) -> float:
    """把 LLM/数据源返回的值安全转成 float（可能是字符串或 None），失败用默认值。"""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _low_confidence_note(state: AgentState) -> str:
    """D3：基于已有量化/情报信号判定低置信度，返回主动求助提示（无则返回空串）。"""
    quant = state.get("quant_assessment") or {}
    intel = state.get("intelligence_assessment") or {}

    strength = _as_float(quant.get("strength_rating"), default=-1.0)
    quant_low = (
        (0 <= strength <= 3)
        or quant.get("volatility_assessment") in ("high", "extreme")
        or bool(quant.get("risk_flags"))
    )

    sentiment = intel.get("sentiment_score")
    try:
        sentiment = float(sentiment)
    except (TypeError, ValueError):
        sentiment = None
    intel_low = (sentiment is not None and abs(sentiment) < 0.15) or intel.get(
        "impact_strength"
    ) == "weak"

    return _LOW_CONFIDENCE_NOTE if (quant_low or intel_low) else ""


_DEGRADED_PREFIX = "（数据暂不可用，以下为降级提示，仅供参考，不构成投资建议）\n\n"


def _fallback_response(state: AgentState) -> str:
    """LLM 不可用时的降级模板回复（纯文本，无Markdown）。

    D2：显式标注降级，避免静默 fallback 误导用户。
    """
    return _DEGRADED_PREFIX + _fallback_response_body(state)


def _fallback_response_body(state: AgentState) -> str:
    """降级模板回复正文（无前缀标注）。"""
    intent = state.get("intent", "chat")
    name = state.get("active_name", "")
    symbol = state.get("active_symbol", "")

    if intent == "analyze" and name:
        tech = state.get("technical_analysis")
        if tech:
            md = technical_analyzer.to_markdown(tech, name)
            # Strip markdown formatting
            md = md.replace("## ", "").replace("**", "").replace("`", "")
            return f"【{name}({symbol}) 技术分析】\n\n{md}"
        return f"【{name}({symbol})】\n\n正在分析中，请稍后重试。"

    if intent == "trade":
        trade_plan = state.get("trade_plan")
        if trade_plan:
            return (
                f"【交易确认】\n\n"
                f"方向: {trade_plan.get('side')}\n"
                f"数量: {trade_plan.get('quantity')}股\n"
                f"价格: {trade_plan.get('current_price')}\n"
                f"风险等级: {trade_plan.get('risk_level')}"
            )
        return "正在准备交易计划..."

    if intent == "portfolio":
        portfolio = state.get("portfolio_summary")
        if portfolio:
            pnl = portfolio.get("total_pnl", 0)
            return (
                f"【持仓概览】\n\n"
                f"总资产: {portfolio.get('total_assets', 0):,.2f}\n"
                f"盈亏: {pnl:+,.2f}"
            )
        return "暂无持仓数据。"

    return "收到你的消息。我是一个A股模拟投资系统，可以帮你分析股票、模拟交易、查看持仓。请告诉我你想了解什么？"
