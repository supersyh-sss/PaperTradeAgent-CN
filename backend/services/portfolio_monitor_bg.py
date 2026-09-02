"""后台持仓监控 - 交易时间持久化监控 + 分批买卖纪律

核心职责：
  1. 交易时段每 60 秒、非交易时段每 300 秒巡检一次持仓；
  2. 严守风控纪律，按阈值生成「预警 / 减仓 / 果断离场 / 分批止盈止损」建议；
  3. 主动把建议推送到当前活跃会话（实时 SSE 弹窗），并持久化进会话历史，
     请示用户是否按策略分批买入 / 卖出，而非静默地让用户一次性重仓或清仓。

纪律原则：
  - 好的买入 = 分多次、看市场情况买入，而非一次性重仓；
  - 好的卖出 = 分多次、看市场情况卖出，而非一次性清仓；
  - 触碰纪律红线（组合亏损超过阈值）必须果断离场。
"""

import asyncio
import json
import logging
import time as _time
from datetime import datetime, timedelta, timezone

from . import db as db_service
from .live_prices import get_cached_price
from .session_manager import get_active_session, push_system_message
from .symbol import pure_code
from .trading_time import TradingTimeChecker

BJT = timezone(timedelta(hours=8))
logger = logging.getLogger(__name__)

# ── 风控纪律阈值 ──
DAILY_CHANGE_WARN_PCT = 3.0  # 个股日内涨跌幅超过 ±3% 告警
SINGLE_DRAWDOWN_DANGER_PCT = -8.0  # 单只持仓亏损超过 8% 果断减仓
TOTAL_DRAWDOWN_WARN_PCT = -2.0  # 组合回撤超过 2% 预警
TOTAL_DRAWDOWN_DANGER_PCT = -5.0  # 组合亏损超过 5% 触发风控红线，果断离场

# ── 同一建议的去重冷却（秒），避免每个巡检周期重复推送刷屏 ──
_DANGER_COOLDOWN = 300  # 红线建议 5 分钟内只推一次
_WARN_COOLDOWN = 900  # 一般预警 15 分钟内只推一次

# 建议去重键 -> 上次推送时间（单调时钟）
_last_alert_at: dict = {}


def _cooldown_ok(key: str, severity: str) -> bool:
    """判断该建议是否已过冷却期，可再次推送。"""
    now = _time.monotonic()
    cooldown = _DANGER_COOLDOWN if severity == "danger" else _WARN_COOLDOWN
    last = _last_alert_at.get(key, 0)
    if now - last < cooldown:
        return False
    _last_alert_at[key] = now
    return True


async def _target_session() -> str:
    """确定建议的目标会话：优先活跃会话，其次最近一次会话。"""
    session_id = get_active_session()
    if session_id:
        return session_id
    latest = await db_service.get_user_conversations("default", limit=1)
    if latest:
        return latest[0]["id"]
    return ""


async def _persist(session_id: str, body: str, meta: dict):
    """把建议持久化进会话历史（仅对已有内容的真实会话，避免凭空新建空会话）。"""
    try:
        existing = await db_service.get_conversation_messages(session_id)
        if not existing:
            return
        await db_service.create_conversation(session_id, "default")
        await db_service.save_message(
            session_id,
            "assistant",
            body,
            metadata=json.dumps({"monitor": True, **meta}, ensure_ascii=False),
        )
    except Exception:
        logger.warning("监控建议持久化失败", exc_info=True)


async def _emit(suggestion: dict):
    """推送一条纪律建议：实时 SSE + 会话历史持久化。"""
    session_id = await _target_session()
    if session_id:
        push_system_message(
            session_id, {"type": "monitor_suggestion", "data": suggestion}
        )
        await _persist(
            session_id,
            suggestion.get("message", ""),
            {
                "severity": suggestion.get("severity", "info"),
                "action": suggestion.get("action"),
                "symbol": suggestion.get("symbol"),
            },
        )
    else:
        logger.info("暂无可用会话接收监控建议，跳过推送: %s", suggestion.get("title"))


def _build_suggestion(
    severity: str,
    title: str,
    message: str,
    action: str,
    symbol: str | None = None,
    name: str | None = None,
    suggested_prompt: str | None = None,
    positions: list | None = None,
    total_pnl_pct: float = 0.0,
) -> dict:
    """组装一条结构化的纪律建议（供前端渲染风控卡片与一键操作）。"""
    return {
        "severity": severity,
        "title": title,
        "message": message,
        "action": action,
        "symbol": symbol,
        "name": name,
        "suggested_prompt": suggested_prompt,
        "positions": positions or [],
        "total_pnl_pct": round(total_pnl_pct, 2),
        "time_context": TradingTimeChecker.full_time_context(),
        "timestamp": datetime.now(BJT).isoformat(),
    }


async def start_portfolio_monitor():
    """启动后台持仓监控循环（随 FastAPI lifespan 启停）。"""
    user_id = "default"
    logger.info("持仓监控后台任务已启动（交易时间持久化监控 + 分批买卖纪律）")

    while True:
        try:
            interval = 60 if TradingTimeChecker.is_trading_time() else 300

            positions = await db_service.get_all_positions(user_id)
            if not positions:
                await asyncio.sleep(interval)
                continue

            position_snapshots = []
            total_market_value = 0.0
            total_cost = 0.0
            suggestions = []

            for pos in positions:
                symbol = pure_code(pos.get("symbol", ""))
                if not symbol:
                    continue
                qty = pos.get("quantity", 0)
                if qty <= 0:
                    continue

                name = pos.get("name", symbol)
                avg_cost = float(pos.get("avg_cost", 0))
                total_cost += avg_cost * qty

                cache = get_cached_price(symbol)
                current_price = float(cache.get("last_price", 0) or 0) if cache else 0.0
                prev_close = float(cache.get("prev_close", 0) or 0) if cache else 0.0
                if current_price <= 0:
                    current_price = avg_cost

                market_value = current_price * qty
                total_market_value += market_value
                pnl = (current_price - avg_cost) * qty
                pnl_pct = (
                    ((current_price - avg_cost) / avg_cost * 100)
                    if avg_cost > 0
                    else 0.0
                )
                daily_change_pct = (
                    ((current_price - prev_close) / prev_close * 100)
                    if prev_close > 0
                    else 0.0
                )

                position_snapshots.append(
                    {
                        "symbol": symbol,
                        "name": name,
                        "quantity": qty,
                        "avg_cost": round(avg_cost, 3),
                        "current_price": round(current_price, 3),
                        "market_value": round(market_value, 2),
                        "pnl": round(pnl, 2),
                        "pnl_pct": round(pnl_pct, 2),
                        "daily_change_pct": round(daily_change_pct, 2),
                    }
                )

                # 1) 单只持仓亏损触发风控线 -> 果断减仓离场
                if pnl_pct <= SINGLE_DRAWDOWN_DANGER_PCT:
                    key = f"danger:single:{symbol}"
                    if _cooldown_ok(key, "danger"):
                        suggestions.append(
                            _build_suggestion(
                                "danger",
                                f"{name} 触发个股风控线",
                                f"{name} 已亏损 {pnl_pct:+.2f}%，触发个股风控纪律。"
                                f"建议果断减仓离场该股（可分批卖出以控制冲击成本）。是否执行减仓？",
                                "exit",
                                symbol,
                                name,
                                f"我持有的{name}亏损较大，请帮我制定减仓离场计划",
                                position_snapshots,
                                pnl_pct,
                            )
                        )
                # 2) 个股日内波动过大 -> 分批止盈 / 止损提示
                elif abs(daily_change_pct) > DAILY_CHANGE_WARN_PCT:
                    key = f"warn:daily:{symbol}"
                    if _cooldown_ok(key, "warning"):
                        if daily_change_pct > 0:
                            action, verb = "trim", "止盈"
                            title = f"{name} 日内大涨 {daily_change_pct:+.2f}%"
                            prompt = f"我持有的{name}日内上涨，请帮我制定分批止盈计划"
                            note = "可考虑分批止盈、落袋为安，而非一次性全部卖出。"
                        else:
                            action, verb = "trim", "止损"
                            title = f"{name} 日内下跌 {daily_change_pct:+.2f}%"
                            prompt = f"我持有的{name}日内下跌，请帮我制定分批止损计划"
                            note = "注意风险，可考虑分批止损，而非扛单。"
                        suggestions.append(
                            _build_suggestion(
                                "warning",
                                title,
                                f"{name} 日内{daily_change_pct:+.2f}%，{note}是否按计划分批{verb}？",
                                action,
                                symbol,
                                name,
                                prompt,
                                position_snapshots,
                                pnl_pct,
                            )
                        )

            # 组合层面：总盈亏纪律
            total_pnl = total_market_value - total_cost
            total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0.0

            if total_pnl_pct <= TOTAL_DRAWDOWN_DANGER_PCT:
                key = "danger:total"
                if _cooldown_ok(key, "danger"):
                    suggestions.append(
                        _build_suggestion(
                            "danger",
                            "触发组合风控红线",
                            f"持仓组合总亏损 {total_pnl_pct:+.2f}%（超过 {abs(TOTAL_DRAWDOWN_DANGER_PCT):.0f}% 风控线）。"
                            f"纪律要求果断离场：优先分批减仓亏损最大的持仓，控制风险。是否立即执行离场？",
                            "exit",
                            None,
                            None,
                            "触发风控红线，请帮我制定分批减仓离场计划",
                            position_snapshots,
                            total_pnl_pct,
                        )
                    )
            elif total_pnl_pct <= TOTAL_DRAWDOWN_WARN_PCT:
                key = "warn:total"
                if _cooldown_ok(key, "warning"):
                    suggestions.append(
                        _build_suggestion(
                            "warning",
                            "组合回撤预警",
                            f"持仓组合已回撤 {total_pnl_pct:+.2f}%，建议分批减仓控制风险"
                            f"（可分 2-3 批，看市场情况逐步执行）。是否继续减仓？",
                            "trim",
                            None,
                            None,
                            "组合回撤，请帮我制定分批减仓计划",
                            position_snapshots,
                            total_pnl_pct,
                        )
                    )

            for sug in suggestions:
                await _emit(sug)

            if suggestions:
                logger.info(
                    f"持仓监控：{len(positions)} 个持仓，推送 {len(suggestions)} 条纪律建议"
                )
            else:
                logger.debug(f"持仓监控：{len(positions)} 个持仓，无新建议")

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("持仓监控异常")

        await asyncio.sleep(interval)
