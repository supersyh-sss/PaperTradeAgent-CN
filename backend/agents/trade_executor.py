"""交易执行员 Agent - 多因子智能定价 + 费用预估 + 交易周期预测"""

import json as json_mod
import logging
import time as _time
from datetime import datetime

from ..services import db
from ..services.agent_memory import (
    compute_query_hash,
    get_agent_memory,
    save_agent_memory,
)
from ..services.data_source_manager import data_source_manager
from ..services.fee_calculator import calculate_fee, format_fee_estimate
from ..services.llm import choose_client
from ..services.quant_prediction import predict_medium_long_term, predict_short_term
from ..services.symbol import exchange_prefix
from ..services.trade_rules import suggest_lot_size
from ..services.trading_time import TradingTimeChecker
from .prompts import AGENT_PROFILES, TRADE_EXECUTOR_SYSTEM
from .state import AgentState
from .utils import maybe_attach_followup, safe_int

# 盘中交易确认窗口：用户需在60秒内确认，超时自动取消
TRADE_CONFIRM_TIMEOUT_SEC = 60

logger = logging.getLogger(__name__)


async def _get_historical_range(symbol: str) -> float:
    """从K线缓存获取历史日均振幅"""
    try:
        from ..services.kline_cache import get_cached_kline

        kline = get_cached_kline(symbol, "day", 30)
        if not kline:
            return 0
        ranges = []
        for k in kline:
            h = k.get("high", 0) or 0
            l = k.get("low", 0) or 0
            c = k.get("close", 0) or k.get("open", 0) or 1
            if c > 0 and h > l:
                ranges.append((h - l) / c)
        return sum(ranges) / len(ranges) if ranges else 0
    except Exception:
        return 0


async def _get_multi_factor_price(
    symbol: str,
    side: str,
    base_price: float,
    prev_close: float,
    tech: dict,
    intel: dict,
    is_trading: bool,
) -> dict:
    """多因子智能定价"""
    factors = {"base_price": base_price, "adjustments": []}

    # 1. 历史振幅因子（30日）
    hist_range = await _get_historical_range(symbol)
    if hist_range > 0:
        range_adj = hist_range * 0.3
        if side == "BUY":
            factors["hist_range_buy_adj"] = +range_adj * base_price
            factors["adjustments"].append(f"历史波动(+{range_adj * 100:.2f}%)")
        else:
            factors["hist_range_sell_adj"] = -range_adj * base_price
            factors["adjustments"].append(f"历史波动(-{range_adj * 100:.2f}%)")
        factors["hist_daily_range_pct"] = round(hist_range * 100, 2)

    # 2. 技术面因子（Bollinger/MA）
    if tech:
        bb = tech.get("bollinger", {})
        ma20 = tech.get("ma", {}).get("ma20", 0)
        # support/resistance 是列表 [近, 中, 远] 或者标量，统一取最近值
        _raw_resistance = tech.get("resistance", 0)
        _raw_support = tech.get("support", 0)
        resistance = (
            _raw_resistance[-1]
            if isinstance(_raw_resistance, list) and _raw_resistance
            else (_raw_resistance or 0)
        )
        support = (
            _raw_support[0]
            if isinstance(_raw_support, list) and _raw_support
            else (_raw_support or 0)
        )
        if side == "BUY" and support and base_price > support:
            tech_adj = max(0, (base_price - support) / support)
            impact_pct = tech_adj * 0.3  # 实际对价格的影响比例
            factors["tech_support_adj"] = +impact_pct * base_price
            factors["adjustments"].append(f"支撑位溢价(+{impact_pct * 100:.2f}%)")
        elif side == "SELL" and resistance and base_price < resistance:
            tech_adj = max(0, (resistance - base_price) / base_price)
            impact_pct = tech_adj * 0.3  # 实际对价格的影响比例
            factors["tech_resistance_adj"] = -impact_pct * base_price
            factors["adjustments"].append(f"阻力位折扣(-{impact_pct * 100:.2f}%)")
        if ma20 > 0:
            bb_upper = bb.get("upper", 0)
            bb_lower = bb.get("lower", 0)
            if side == "BUY" and bb_lower > 0:
                factors["bb_lower"] = bb_lower
            elif side == "SELL" and bb_upper > 0:
                factors["bb_upper"] = bb_upper

    # 3. 消息面/情绪因子
    if intel:
        sentiment = intel.get("sentiment_score", 0)  # -1 to 1
        if abs(sentiment) > 0.1:
            sent_adj = sentiment * 0.02
            factors["sentiment_adj"] = sent_adj * base_price
            factors["adjustments"].append(
                f"情绪因子({'利好' if sentiment > 0 else '利空'}: {sent_adj * 100:+.2f}%)"
            )

        impact = intel.get("impact", {})
        if impact:
            direction = impact.get("direction", "")
            strength = impact.get("strength", "")
            impact_weights = {"weak": 0.01, "moderate": 0.03, "strong": 0.05}
            if direction == "利好" and side == "BUY":
                factors["impact_adj"] = +impact_weights.get(strength, 0.01) * base_price
                factors["adjustments"].append(
                    f"消息利多(+{impact_weights.get(strength, 0.01) * 100:.1f}%)"
                )
            elif direction == "利空" and side == "SELL":
                factors["impact_adj"] = -impact_weights.get(strength, 0.01) * base_price
                factors["adjustments"].append(
                    f"消息利空(-{impact_weights.get(strength, 0.01) * 100:.1f}%)"
                )

    # 4. 非交易时段：预测下一交易日开盘价
    if not is_trading:
        open_dev = await _predict_open_gap(symbol, prev_close)
        factors["open_gap_est"] = open_dev * base_price
        factors["adjustments"].append(f"开盘预测调整({open_dev * 100:+.2f}%)")

    # 计算最终估价
    total_adj = 0.0
    for k, v in factors.items():
        if "adj" in k and isinstance(v, (int, float)):
            total_adj += v

    estimated = round(base_price + total_adj, 2)
    # 确保不低于1分钱
    estimated = max(estimated, 0.01)

    factors["estimated_price"] = estimated
    factors["adjustment_total"] = round(total_adj, 2)
    factors["adjustment_pct"] = (
        round(total_adj / base_price * 100, 2) if base_price else 0
    )

    return factors


async def _predict_open_gap(symbol: str, prev_close: float) -> float:
    """基于近期数据预测次日开盘偏离度"""
    try:
        from ..services.kline_cache import get_cached_kline

        kline = get_cached_kline(symbol, "day", 10)
        if not kline or len(kline) < 3:
            return 0
        # 计算最近3天的开盘/昨收偏离均值
        gaps = []
        for i in range(1, min(4, len(kline))):
            today_open = kline[i].get("open", 0) or 0
            yesterday_close = kline[i - 1].get("close", 0) or 0
            if yesterday_close > 0:
                gaps.append((today_open - yesterday_close) / yesterday_close)

        # 融合整体趋势方向
        trend_gap = sum(gaps) / len(gaps) if gaps else 0

        # 限制预测范围（-3% ~ +3%）
        return max(-0.03, min(0.03, trend_gap))
    except Exception:
        return 0


async def trade_executor_node(state: AgentState) -> AgentState:
    """交易执行员节点：准备交易计划（融合技术面+消息面）+ LLM评估"""
    symbol = state.get("active_symbol")
    name = state.get("active_name", "")
    user_id = state.get("user_id", "default")

    if not symbol:
        state["trade_plan"] = None
        return state

    is_trading = TradingTimeChecker.is_trading_time()
    state["is_trading_time"] = is_trading

    # 获取实时价格
    market_data = await data_source_manager.get_realtime([symbol])
    stock_info = next(iter(market_data.values()), None)

    current_price = stock_info.get("price", 0) if stock_info else 0
    prev_close = (
        stock_info.get("prev_close", current_price) if stock_info else current_price
    )
    state["market_data"] = market_data

    tech = state.get("technical_analysis") or {}
    intel = state.get("market_intelligence") or {}
    fund = state.get("fundamental_analysis") or {}
    time_horizon = state.get("time_horizon", "long") or "long"
    trade_side = state.get("trade_side") or "BUY"
    trade_quantity = safe_int(state.get("trade_quantity")) or 100

    trade_plan = {
        "symbol": symbol,
        "name": name,
        "side": trade_side,
        "quantity": trade_quantity,
        "current_price": current_price,
        "prev_close": prev_close,
        "is_trading_time": is_trading,
        "time_horizon": time_horizon,
        "horizon_label": "短线（3-5分钟）"
        if time_horizon == "short"
        else "中长线（日/月/年）",
        "fundamental": fund,
        "warnings": [],
        "recommendations": [],
        "risk_level": "NORMAL",
        "estimated_note": "",
    }

    # 基本面风险（估值过高 → 提示谨慎）
    if fund and fund.get("valuation"):
        _val = fund["valuation"]
        if "高估" in _val or "偏高" in _val:
            trade_plan["warnings"].append(
                {
                    "source": "基本面",
                    "level": "WARNING",
                    "message": f"估值{_val}（PE {fund.get('pe')}，PB {fund.get('pb')}），追高需谨慎",
                }
            )
        elif "低估" in _val:
            trade_plan["recommendations"].append(
                {
                    "source": "基本面",
                    "level": "OPPORTUNITY",
                    "message": f"估值{_val}（PE {fund.get('pe')}，PB {fund.get('pb')}），具备安全边际",
                }
            )

    # 技术面风险
    if tech:
        rsi = tech.get("rsi", 50)
        if rsi > 70:
            trade_plan["warnings"].append(
                {
                    "source": "技术面",
                    "level": "WARNING",
                    "message": f"RSI超买({rsi})，当前价格可能偏高",
                }
            )
        elif rsi < 30:
            trade_plan["recommendations"].append(
                {
                    "source": "技术面",
                    "level": "OPPORTUNITY",
                    "message": f"RSI超卖({rsi})，可能是低位买入机会",
                }
            )

    # 消息面风险
    if intel:
        risk_alerts = intel.get("risk_alerts", [])
        for alert in risk_alerts:
            trade_plan["warnings"].append(
                {
                    "source": "消息面",
                    "level": alert.get("level", "MEDIUM"),
                    "message": f"[{alert.get('type', '风险')}] {alert.get('title', '')}",
                }
            )
            trade_plan["risk_level"] = "HIGH"

        impact = intel.get("impact", {})
        if impact.get("direction") == "利空" and impact.get("strength") == "strong":
            trade_plan["warnings"].append(
                {
                    "source": "消息面",
                    "level": "HIGH",
                    "message": f"近期有重大利空消息（{impact.get('reason', '')}），建议谨慎操作",
                }
            )
            trade_plan["risk_level"] = "HIGH"
        elif impact.get("direction") == "利好" and impact.get("strength") == "strong":
            trade_plan["recommendations"].append(
                {
                    "source": "消息面",
                    "level": "OPPORTUNITY",
                    "message": f"近期有重大利好消息（{impact.get('reason', '')}），可关注买入机会",
                }
            )

    # 综合风险等级
    warning_count = len([w for w in trade_plan["warnings"] if w["level"] == "HIGH"])
    if warning_count >= 2:
        trade_plan["risk_level"] = "CRITICAL"
    elif warning_count == 1:
        trade_plan["risk_level"] = "HIGH"

    # ── 交易周期智能定价 ─────────────────────────────────────────
    # 短线：分钟级（3-5分钟）预测，偏差在「可成交」与「不过度追价」间平衡；
    # 中长线：日/月/年级多因子定价 + 目标价预测。
    if time_horizon == "short":
        pricing = await predict_short_term(symbol, current_price, trade_side)
        estimated_price = pricing["predicted_price"]
        factors_display = [pricing["note"]]
        adjustment_pct = pricing.get("deviation_pct", 0)
        pricing_note = pricing["note"]
    else:
        pricing = await _get_multi_factor_price(
            symbol, trade_side, current_price, prev_close, tech, intel, is_trading
        )
        estimated_price = pricing["estimated_price"]
        factors_display = pricing["adjustments"]
        adjustment_pct = pricing["adjustment_pct"]
        pricing_note = f"多因子定价: 基础{current_price}, 调整{adjustment_pct:+.2f}% -> {estimated_price}"
        # 中长线目标价（日/月/年级别）
        try:
            pricing["horizon_prediction"] = await predict_medium_long_term(
                symbol, current_price, trade_side, tech, "long"
            )
        except Exception:
            logger.warning("中长线目标价预测失败", exc_info=True)

    trade_plan["suggested_price"] = estimated_price
    trade_plan["pricing_factors"] = pricing
    trade_plan["smart_pricing"] = {
        "current_price": current_price,
        "suggested_price": estimated_price,
        "factors": factors_display,
        "adjustment_pct": adjustment_pct,
        "note": pricing_note,
        "horizon": time_horizon,
    }

    # 价格笼子校验：确保估价在 ±2% 范围内以保证高填充概率
    price_validated = True
    price_validation_note = ""
    price_cage_lower = round(current_price * 0.98, 2)
    price_cage_upper = round(current_price * 1.02, 2)
    if estimated_price < price_cage_lower or estimated_price > price_cage_upper:
        clamped_price = max(price_cage_lower, min(estimated_price, price_cage_upper))
        price_validation_note = f"估价 {estimated_price} 超出价格笼子 [{price_cage_lower}, {price_cage_upper}]，调整为 {clamped_price} 以确保高填充概率"
        estimated_price = clamped_price
        price_validated = False
    else:
        price_validation_note = f"估价 {estimated_price} 在价格笼子 [{price_cage_lower}, {price_cage_upper}] 内，填充概率高"
    trade_plan["suggested_price"] = estimated_price
    trade_plan["price_validated"] = price_validated
    trade_plan["price_validation_note"] = price_validation_note
    trade_plan["smart_pricing"]["suggested_price"] = estimated_price
    trade_plan["smart_pricing"]["price_validated"] = price_validated

    # 非交易时间：记录预估挂单信息
    if not is_trading and current_price:
        trade_plan["estimated_price"] = {
            "base_price": current_price,
            "estimated_price": estimated_price,
            "adjustment_pct": adjustment_pct,
            "note": f"非交易时间{('短线' if time_horizon == 'short' else '中长线')}预估价: {estimated_price}",
            "open_gap_prediction": pricing.get("open_gap_est", 0),
        }

    # Suggested Quantity: 建议股数
    # 建议量必须基于“可用”口径：余额需扣除在途买单锁定，可卖量需扣除 T+1 冻结与在途卖单
    try:
        account = await db.get_account(user_id)
        balance = account.get("balance", 0) if account else 0
    except Exception:
        from ..config import INITIAL_BALANCE

        balance = INITIAL_BALANCE
    from ..services.order_engine import get_locked_balance, get_locked_shares

    try:
        locked_cash = get_locked_balance(user_id)
    except Exception:
        locked_cash = 0.0
    available_cash = max(round(balance - locked_cash, 2), 0)

    if trade_side == "SELL":
        try:
            pos = await db.get_position(user_id, symbol)
            holding = pos.get("quantity", 0) if pos else 0
        except Exception:
            holding = 0
        try:
            from ..services.position_service import compute_sellable

            _, sellable = compute_sellable(pos or {})
        except Exception:
            sellable = holding
        try:
            locked_share_qty = get_locked_shares(user_id, symbol)
        except Exception:
            locked_share_qty = 0
        max_sellable = max(sellable - locked_share_qty, 0)
        suggested_qty = min(trade_quantity or max_sellable, max_sellable)
        suggested_qty = (suggested_qty // 100) * 100
    else:
        suggested_qty = suggest_lot_size(available_cash, estimated_price, max_pct=0.5)
    trade_plan["suggested_quantity"] = max(suggested_qty, 100)
    trade_plan["quantity"] = trade_plan[
        "suggested_quantity"
    ]  # 同步 quantity 与 suggested_quantity
    trade_plan["user_balance"] = available_cash
    trade_plan["locked_balance"] = round(locked_cash, 2)

    # 手续费预估
    exchange = exchange_prefix(symbol)
    trade_amount = estimated_price * trade_plan["suggested_quantity"]
    fee, fee_breakdown = calculate_fee(trade_amount, trade_side, exchange)
    trade_plan["estimated_fee"] = fee
    trade_plan["fee_breakdown"] = fee_breakdown
    trade_plan["fee_note"] = format_fee_estimate(
        estimated_price, trade_plan["suggested_quantity"], trade_side, exchange
    )
    trade_plan["total_cost"] = (
        trade_amount + fee if trade_side == "BUY" else trade_amount - fee
    )

    # 交易纪律：基于 ATR 的止损/止盈建议（短线更紧，中长线更宽）
    ext = tech.get("extended") or {}
    atr = float(ext.get("atr", 0) or 0) or (
        current_price * 0.02 if current_price else 0
    )
    atr_pct = atr / current_price if current_price else 0.02
    if time_horizon == "short":
        stop_pct = 0.01
        take_pct = 0.015
    else:
        stop_pct = max(atr_pct * 1.5, 0.02)
        take_pct = max(atr_pct * 2.0, 0.03)
    if trade_side == "BUY":
        discipline = {
            "stop_loss": round(estimated_price * (1 - stop_pct), 2),
            "take_profit": round(estimated_price * (1 + take_pct), 2),
            "stop_loss_pct": round(stop_pct * 100, 2),
            "take_profit_pct": round(take_pct * 100, 2),
            "note": f"止损 -{stop_pct * 100:.1f}% / 止盈 +{take_pct * 100:.1f}%（{'短线' if time_horizon == 'short' else '中长线'}ATR纪律）",
        }
    else:
        discipline = {
            "stop_loss": round(estimated_price * (1 + stop_pct), 2),
            "take_profit": round(estimated_price * (1 - take_pct), 2),
            "stop_loss_pct": round(stop_pct * 100, 2),
            "take_profit_pct": round(take_pct * 100, 2),
            "note": f"止损 +{stop_pct * 100:.1f}% / 止盈 -{take_pct * 100:.1f}%（{'短线' if time_horizon == 'short' else '中长线'}ATR纪律）",
        }
    trade_plan["discipline"] = discipline

    # 1分钟交易窗口标记
    trade_plan["plan_created_at"] = _time.time()
    trade_plan["plan_timeout_sec"] = TRADE_CONFIRM_TIMEOUT_SEC if is_trading else None
    if is_trading:
        trade_plan["timeout_note"] = (
            f"请在 {TRADE_CONFIRM_TIMEOUT_SEC} 秒内确认交易，超时将自动取消（盘中价格变化快）"
        )
    else:
        trade_plan["timeout_note"] = "非交易时间无超时限制，挂单有效期至下一交易日开盘"

    state["trade_plan"] = trade_plan

    # 非交易时间：准备挂单信息
    if not is_trading:
        trade_plan["pending_order_note"] = (
            "当前为非交易时间，确认后将创建挂单，在下一交易日自动执行。"
        )

    # LLM 评估交易计划
    symbol = state.get("active_symbol")
    name = trade_plan.get("name", "")
    trade_side = state.get("trade_side") or trade_plan.get("side") or "BUY"
    trade_quantity = state.get("trade_quantity", 0) or trade_plan.get("quantity", 0)
    current_price = trade_plan.get("current_price", 0)
    risk_level = trade_plan.get("risk_level", "UNKNOWN")

    risk_desc = {
        "LOW": "低风险",
        "NORMAL": "正常风险",
        "MEDIUM": "中等风险",
        "HIGH": "高风险",
        "CRITICAL": "极高风险",
    }.get(risk_level, risk_level)

    lines = [f"{name or symbol} 交易计划：", ""]
    display_qty = trade_plan.get("suggested_quantity", trade_quantity)
    lines.append(
        f"┃ 方向：{trade_side}  |  数量：{display_qty}股  |  风险：{risk_level}（{risk_desc}）"
    )
    lines.append(f"┃ 周期：{trade_plan.get('horizon_label', '中长线')}")
    lines.append(f"┃ 当前价：{current_price}  →  建议价：{estimated_price}")
    lines.append(f"┃ 预计金额：{trade_amount:,.2f}元  |  手续费：{fee:,.2f}元")
    lines.append(f"┃ 交易时段：{'盘中交易' if is_trading else '非交易时间（可挂单）'}")
    if is_trading:
        lines.append(f"┃ ⏱ {trade_plan['timeout_note']}")
    else:
        lines.append(f"┃ {trade_plan['timeout_note']}")

    # 定价因子与交易纪律
    factors = trade_plan.get("smart_pricing", {}).get("factors", [])
    if factors:
        lines.append(f"┃ 定价因子：{' · '.join(factors)}")
    disc = trade_plan.get("discipline") or {}
    if disc.get("note"):
        lines.append(f"┃ 纪律：{disc['note']}")

    # LLM assessment
    try:
        user_input = state.get("user_input", "")
        agent_key = "trade_executor"
        query_hash = compute_query_hash(user_input, agent_key, symbol)

        # 交易计划可复用缓存（但仍需重新验证当前价格）
        cached = await get_agent_memory(
            user_id, agent_key, symbol, query_hash, query=user_input
        )
        llm_result = None
        if cached:
            try:
                llm_result = json_mod.loads(cached)
            except Exception:
                cached = None

        if not cached:
            plan_text = _build_trade_plan_summary(trade_plan, tech, intel)
            current_time = state.get("current_time", "")
            time_prefix = (
                f"Current system time: {current_time}\n\n" if current_time else ""
            )
            # 注入对话上下文 + 上游 Agent 输出
            history_summary = state.get("history_summary", "")
            strategy = state.get("strategy_direction", "")
            quant_assess = state.get("quant_assessment")
            intel_assess = state.get("intelligence_assessment")
            ctx_lines = [f"User asked: {user_input}"]
            if history_summary:
                ctx_lines.append(f"Conversation context: {history_summary}")
            if strategy:
                ctx_lines.append(f"Orchestrator reasoning: {strategy}")
            if quant_assess:
                ctx_lines.append(
                    f"Quant assessment: trend={quant_assess.get('trend_assessment', '?')}, strength={quant_assess.get('strength_rating', '?')}/10, signals={quant_assess.get('key_signals', [])}"
                )
            if intel_assess:
                ctx_lines.append(
                    f"Market intel: sentiment={intel_assess.get('sentiment_label', '?')}({intel_assess.get('sentiment_score', 0):+.2f}), impact={intel_assess.get('impact_direction', '?')}, factors={intel_assess.get('key_factors', [])}"
                )
            ctx_prefix = "\n".join(ctx_lines) + "\n\n"
            messages = [
                {"role": "system", "content": TRADE_EXECUTOR_SYSTEM},
                {"role": "user", "content": ctx_prefix + time_prefix + plan_text},
            ]
            llm_result = await choose_client(True).chat_json(
                messages, temperature=0.1, max_tokens=1024
            )
            if llm_result.get("parse_error"):
                raise ValueError("LLM returned non-JSON")

            await save_agent_memory(
                user_id,
                agent_key,
                symbol,
                query_hash,
                json_mod.dumps(llm_result, ensure_ascii=False, default=str),
                query=user_input,
            )

        state["executor_assessment"] = llm_result

        rec = llm_result.get("trade_recommendation", "N/A")
        rec_desc = {
            "PROCEED": "[建议执行]",
            "CAUTION": "[谨慎操作]",
            "ABORT": "[建议放弃]",
        }.get(rec, rec)
        lines.append(f"● 执行建议：{rec_desc}")

        risk_detail = llm_result.get("risk_assessment_chinese", "")
        if risk_detail:
            lines.append(f"● 风险分析：{risk_detail}")

        warnings_chinese = llm_result.get("warnings_chinese", [])
        if warnings_chinese:
            lines.append("● 风险提示：")
            for w in warnings_chinese[:3]:
                lines.append(f"   - {w}")
    except Exception:
        state["executor_assessment"] = None
        warnings = trade_plan.get("warnings", [])
        if warnings:
            lines.append("● 风险提示：")
            for w in warnings[:3]:
                lines.append(f"   - [{w.get('source', '')}] {w.get('message', '')}")

        recs = trade_plan.get("recommendations", [])
        if recs:
            lines.append("● 建议：")
            for r in recs[:2]:
                lines.append(f"   - [{r.get('source', '')}] {r.get('message', '')}")

    if not is_trading:
        lines.append("● 提示：当前非交易时间，确认后将以挂单方式提交，开盘后自动执行。")

    agent_log_content = "\n".join(lines)

    # Emit agent_log
    agent_log = {
        "agent": "trade_executor",
        "emoji": AGENT_PROFILES["trade_executor"]["emoji"],
        "name_cn": AGENT_PROFILES["trade_executor"]["name_cn"],
        "color": AGENT_PROFILES["trade_executor"]["color"],
        "content": agent_log_content,
        "timestamp": datetime.now().astimezone().isoformat(),
    }
    agent_log = maybe_attach_followup(agent_log, "trade_executor")
    state.setdefault("agent_logs", []).append(agent_log)

    return state


def _build_trade_plan_summary(trade_plan: dict, tech: dict, intel: dict) -> str:
    """Build a text summary of the trade plan for LLM input."""
    lines = [
        f"Trade Plan for {trade_plan.get('name')} ({trade_plan.get('symbol')}):",
        f"Side: {trade_plan.get('side')}",
        f"Quantity: {trade_plan.get('quantity')} shares",
        f"Current Price: {trade_plan.get('current_price')}",
        f"Prev Close: {trade_plan.get('prev_close')}",
        f"Time Horizon: {trade_plan.get('horizon_label')}",
        f"Is Trading Time: {trade_plan.get('is_trading_time')}",
        f"Risk Level (rule-based): {trade_plan.get('risk_level')}",
        f"User Balance: {trade_plan.get('user_balance', 0):,.2f} (available cash)",
    ]

    # 基本面
    fund = trade_plan.get("fundamental") or {}
    if fund:
        lines.append("--- Fundamentals ---")
        lines.append(
            f"PE: {fund.get('pe')}, PB: {fund.get('pb')}, Turnover: {fund.get('turnover')}%, Valuation: {fund.get('valuation')}"
        )

    # Tech summary
    if tech:
        lines.append("--- Technical Analysis ---")
        lines.append(f"Trend: {tech.get('trend')}")
        lines.append(f"RSI: {tech.get('rsi')}")
        bb = tech.get("bollinger", {})
        lines.append(
            f"Bollinger: upper={bb.get('upper')}, middle={bb.get('middle')}, lower={bb.get('lower')}"
        )
        lines.append(f"MA20: {tech.get('ma', {}).get('ma20')}")
        lines.append(f"MACD Signal: {tech.get('macd_signal')}")
        lines.append(
            f"Support: {tech.get('support')}, Resistance: {tech.get('resistance')}"
        )
        lines.append(f"Volatility: {tech.get('volatility')}%")
        ext = tech.get("extended") or {}
        if ext:
            lines.append(
                f"ATR: {ext.get('atr')}({ext.get('atr_pct')}%), KDJ: {ext.get('kdj')}, ADX: {ext.get('adx')}, CCI: {ext.get('cci')}"
            )
        qs = tech.get("quant_score")
        if qs:
            lines.append(f"Quant Score: {qs.get('score')}/100 ({qs.get('label')})")

    # Intel summary
    if intel:
        lines.append("--- Market Intelligence ---")
        lines.append(
            f"Sentiment: {intel.get('sentiment_label')} ({intel.get('sentiment_score')})"
        )
        impact = intel.get("impact", {})
        lines.append(
            f"Impact: {impact.get('direction')}/{impact.get('strength')} - {impact.get('reason')}"
        )

    # 定价与纪律
    sp = trade_plan.get("smart_pricing") or {}
    if sp:
        lines.append(
            f"Suggested Price: {sp.get('suggested_price')} (adjust {sp.get('adjustment_pct', 0):+.2f}%)"
        )
    disc = trade_plan.get("discipline") or {}
    if disc.get("note"):
        lines.append(
            f"Discipline: {disc['note']} (stop {disc.get('stop_loss')}, take {disc.get('take_profit')})"
        )

    # Warnings & Recommendations
    warnings = trade_plan.get("warnings", [])
    if warnings:
        lines.append("--- Warnings ---")
        for w in warnings:
            lines.append(f"[{w.get('level')}][{w.get('source')}] {w.get('message')}")
    recommendations = trade_plan.get("recommendations", [])
    if recommendations:
        lines.append("--- Recommendations ---")
        for r in recommendations:
            lines.append(f"[{r.get('level')}][{r.get('source')}] {r.get('message')}")

    # Estimated price (non-trading)
    est = trade_plan.get("estimated_price", {})
    if est:
        lines.append(
            f"Estimated Price (non-trading): {est.get('estimated_price')} (base: {est.get('base_price')}, adj: {est.get('adjustment_pct', 0)}%)"
        )

    return "\n".join(lines)
