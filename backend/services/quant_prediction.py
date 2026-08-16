"""量化预测引擎：短线（分钟级 3-5 分钟）与中长线（日/月/年）目标价预测。

设计约束：
- 不做超短线（不预测秒级/高频），短线定位为分钟级 3-5 分钟。
- 短线挂单预估价要在「可成交」与「不过度追高/杀跌」之间取平衡，
  避免偏差过大导致用户一进场就浮亏，也避免偏差过小导致挂单无法成交。
- 中长线针对日/月/年级别，采用趋势 + 支撑阻力 + ATR 的稳健估算。
"""
from typing import Dict, Optional

from .quant_signals import compute_atr
from .kline_cache import get_cached_kline, save_kline_to_cache

import numpy as np
import pandas as pd

logger = __import__("logging").getLogger(__name__)


async def _fetch_minute_kline(symbol: str, period: str = "m5", count: int = 96) -> Optional[list]:
    """获取分钟级 K 线（带周期感知的文件缓存，避免污染日线缓存）。"""
    cached = get_cached_kline(symbol, period, days=count)
    if cached:
        return cached
    try:
        from .tencent_api import tencent_api
        data = await tencent_api.get_kline(symbol, period, count)
        if data:
            save_kline_to_cache(symbol, period, data)
        return data
    except Exception as e:
        logger.warning("分钟K线获取失败 %s: %s", symbol, e)
        return None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


async def predict_short_term(symbol: str, current_price: float, side: str) -> dict:
    """短线目标价：基于分钟级 K 线（3-5 分钟）预测。

    返回 predicted_price / deviation_pct / band / note。
    """
    default_dev = 0.003  # 默认 0.3% 偏差
    result = {
        "horizon": "short",
        "horizon_label": "短线（3-5分钟）",
        "current_price": current_price,
        "predicted_price": round(current_price * (1 + default_dev if side == "BUY" else 1 - default_dev), 3),
        "deviation_pct": default_dev * 100,
        "band": None,
        "note": "短线预测：基于分钟级动量",
        "confidence": 0.5,
    }

    kline = await _fetch_minute_kline(symbol, "m5", 96)
    if not kline or len(kline) < 5:
        result["note"] = "分钟数据不足，短线预测退化为轻量偏差（0.3%）"
        return result

    closes = [float(k["close"]) for k in kline]
    highs = [float(k["high"]) for k in kline]
    lows = [float(k["low"]) for k in kline]

    # 近 5 根 5 分钟 K 线的动量（对当前价归一化）
    lookback = min(5, len(closes) - 1)
    momentum = (closes[-1] - closes[-1 - lookback]) / closes[-1 - lookback] if closes[-1 - lookback] else 0.0

    # 分钟级 ATR 百分比
    s_close = pd.Series(closes)
    s_high = pd.Series(highs)
    s_low = pd.Series(lows)
    atr = compute_atr(s_high, s_low, s_close, period=14)
    atr_pct = atr / current_price if current_price else 0.0

    # 平衡偏差：动量为主，ATR 兜底，限制在 [0.15%, 0.9%] 区间
    # —— 偏差太小不易成交，太大则一进场就浮亏。
    base = 0.003
    momentum_adj = _clamp(momentum * 0.5, -0.004, 0.004)
    atr_adj = _clamp(atr_pct * 0.15, 0, 0.003)

    if side == "BUY":
        deviation = _clamp(base + momentum_adj + atr_adj, 0.0015, 0.009)
    else:
        deviation = _clamp(base - momentum_adj + atr_adj, 0.0015, 0.009)

    predicted = round(current_price * (1 + deviation if side == "BUY" else 1 - deviation), 3)
    # 给一个 3-5 分钟的预测区间（正负 0.15%）
    band = [round(current_price * (1 - 0.0015), 3), round(current_price * (1 + 0.0015), 3)]

    result.update({
        "predicted_price": predicted,
        "deviation_pct": round(deviation * 100, 3),
        "band": band,
        "confidence": round(_clamp(0.5 + abs(momentum) * 30, 0.3, 0.85), 2),
        "momentum": round(momentum * 100, 3),
        "atr_pct": round(atr_pct * 100, 3),
        "note": (
            f"短线3-5分钟预测：近{lookback}根5分钟K线动量{'+' if momentum >= 0 else ''}{momentum * 100:.2f}%，"
            f"ATR {atr_pct * 100:.2f}%，{'买入' if side == 'BUY' else '卖出'}目标价 {predicted} "
            f"（偏差{deviation * 100:.2f}%，兼顾可成交与不过度追价）"
        ),
    })
    return result


async def predict_medium_long_term(symbol: str, current_price: float, side: str,
                                   tech: dict, horizon: str) -> dict:
    """中长线目标价：日/月/年级别，趋势 + 支撑阻力 + ATR 稳健估算。

    horizon: "medium"（日/周）| "long"（月/年）。
    """
    label = "中长线（日/月/年）" if horizon == "long" else "中线（日/周）"
    result = {
        "horizon": horizon,
        "horizon_label": label,
        "current_price": current_price,
        "predicted_price": current_price,
        "deviation_pct": 0.0,
        "band": None,
        "note": "",
        "confidence": 0.5,
    }

    if not tech:
        result["note"] = "技术面数据不足，中长线目标价暂按现价估算"
        return result

    support = tech.get("support") or []
    resistance = tech.get("resistance") or []
    ma = tech.get("ma", {})
    trend = tech.get("trend", "neutral")
    ext = tech.get("extended") or {}
    atr_pct = float(ext.get("atr_pct", 0) or 0) / 100.0

    # 支撑/阻力取最近一层（列表可能是 [近, 中, 远]）
    sup = support[0] if isinstance(support, list) and support else (support or 0)
    res = resistance[-1] if isinstance(resistance, list) and resistance else (resistance or 0)

    if side == "BUY":
        # 买入：趋势回调时挂支撑位附近，趋势向上时小幅溢价
        if trend == "bullish":
            base = float(ma.get("ma20", current_price) or current_price)
            deviation = _clamp(atr_pct * 0.4, 0.002, 0.015)
            target = current_price * (1 + deviation)
            target = max(target, base)
        else:
            target = sup if sup and sup > 0 else current_price * 0.995
        predicted = round(_clamp(target, current_price * 0.97, current_price * 1.02), 3)
    else:
        # 卖出：趋势走弱时挂阻力位附近，趋势向下时小幅折价
        if trend == "bearish":
            base = float(ma.get("ma20", current_price) or current_price)
            deviation = _clamp(atr_pct * 0.4, 0.002, 0.015)
            target = current_price * (1 - deviation)
            target = min(target, base)
        else:
            target = res if res and res > 0 else current_price * 1.005
        predicted = round(_clamp(target, current_price * 0.98, current_price * 1.03), 3)

    deviation = (predicted - current_price) / current_price if current_price else 0.0
    result.update({
        "predicted_price": predicted,
        "deviation_pct": round(deviation * 100, 3),
        "support": sup,
        "resistance": res,
        "note": (
            f"{label}预测：趋势{trend}，支撑{sup} / 阻力{res}，"
            f"{'买入' if side == 'BUY' else '卖出'}目标价 {predicted}（偏差{deviation * 100:.2f}%）"
        ),
    })
    return result
