"""算法预测服务 - 技术指标+量价关系的短期价格预测"""


def predict_short_term(kline_data: list[dict], lookback: int = 20) -> dict:
    """
    基于1分钟/5分钟K线的短期价格走势预测。
    返回：趋势方向、置信度、目标价位、止损建议。
    """
    if not kline_data or len(kline_data) < lookback:
        return {"trend": "insufficient_data", "confidence": 0, "signals": []}

    recent = kline_data[-lookback:]
    closes = [k["close"] for k in recent]
    volumes = [k.get("volume", 0) for k in recent]
    highs = [k["high"] for k in recent]
    lows = [k["low"] for k in recent]

    signals = []

    # 1. 动量信号：最近N分钟的涨跌趋势
    momentum = _calc_momentum(closes, period=5)
    if momentum > 0.5:
        signals.append(
            {
                "type": "momentum",
                "direction": "up",
                "strength": round(momentum, 2),
                "desc": f"5周期动量{'+' if momentum > 0 else ''}{momentum:.1%}，短期偏多",
            }
        )
    elif momentum < -0.5:
        signals.append(
            {
                "type": "momentum",
                "direction": "down",
                "strength": round(abs(momentum), 2),
                "desc": f"5周期动量{momentum:.1%}，短期偏空",
            }
        )

    # 2. 成交量异动：放量/缩量
    vol_signal = _volume_anomaly(volumes)
    if vol_signal:
        signals.append(vol_signal)

    # 3. 日内波动区间预测
    recent_atr = _calc_atr(highs, lows, closes, period=10)
    current = closes[-1]
    upper = round(current + recent_atr * 1.5, 2)
    lower = round(current - recent_atr * 1.5, 2)
    signals.append(
        {
            "type": "range",
            "upper": upper,
            "lower": lower,
            "desc": f"基于ATR({recent_atr:.2f})，短期波动区间 [{lower}, {upper}]",
        }
    )

    # 4. 支撑/阻力突破概率
    support = min(lows[-10:])
    resistance = max(highs[-10:])
    breakout = _breakout_probability(closes, support, resistance)
    signals.append(breakout)

    # 5. 综合趋势判断
    buy_signals = sum(1 for s in signals if s.get("direction") == "up")
    sell_signals = sum(1 for s in signals if s.get("direction") == "down")
    total_sig = max(buy_signals + sell_signals, 1)
    confidence = round((max(buy_signals, sell_signals)) / total_sig, 2)

    if buy_signals > sell_signals:
        trend = "bullish"
    elif sell_signals > buy_signals:
        trend = "bearish"
    else:
        trend = "neutral"

    last_close = closes[-1]
    if trend == "bullish":
        target = round(last_close * (1 + abs(momentum) * 0.01), 2)
        stop_loss = round(lower, 2)
    elif trend == "bearish":
        target = round(last_close * (1 - abs(momentum) * 0.01), 2)
        stop_loss = round(upper, 2)
    else:
        target = last_close
        stop_loss = last_close

    return {
        "trend": trend,
        "confidence": confidence,
        "current_price": last_close,
        "target_price": target,
        "stop_loss": stop_loss,
        "signals": signals,
        "atr": round(recent_atr, 2),
    }


def technical_divergence(
    close_prices: list[float], rsi_values: list[float]
) -> dict | None:
    """
    RSI背离检测：价格上涨但RSI下降 = 看跌背离，价格下跌但RSI上升 = 看涨背离。
    """
    if len(close_prices) < 10 or len(rsi_values) < 10:
        return None

    # 取最近两段高/低点
    half = max(len(close_prices) // 2, 5)
    recent_closes = close_prices[-half:]
    recent_rsi = rsi_values[-half:]
    older_closes = close_prices[-len(close_prices) : -half]
    older_rsi = rsi_values[-len(rsi_values) : -half]

    # 价格新高但RSI未新高 → 顶背离
    if max(recent_closes) > max(older_closes) and max(recent_rsi) < max(older_rsi):
        return {
            "type": "bearish_divergence",
            "severity": "high",
            "desc": "RSI顶背离：价格创新高但RSI未跟上，上涨动能减弱，注意回调风险",
        }

    # 价格新低但RSI未新低 → 底背离
    if min(recent_closes) < min(older_closes) and min(recent_rsi) > min(older_rsi):
        return {
            "type": "bullish_divergence",
            "severity": "medium",
            "desc": "RSI底背离：价格创新低但RSI未跟进，下跌动能减弱，可能反弹",
        }

    return None


def volume_price_analysis(close_prices: list[float], volumes: list[float]) -> dict:
    """
    量价关系分析：放量上涨=健康，缩量上涨=警惕，放量下跌=恐慌，缩量下跌=惜售。
    """
    if len(close_prices) < 5 or len(volumes) < 5:
        return {"signal": "unknown", "desc": "数据不足"}

    # 最近5周期量价
    price_change = (
        (close_prices[-1] - close_prices[-5]) / close_prices[-5] * 100
        if close_prices[-5]
        else 0
    )
    avg_vol = sum(volumes[-5:]) / 5
    prev_avg_vol = sum(volumes[-10:-5]) / 5 if len(volumes) >= 10 else avg_vol
    vol_change = (avg_vol / prev_avg_vol - 1) * 100 if prev_avg_vol > 0 else 0

    if price_change > 0 and vol_change > 20:
        signal = "bullish_volume"
        desc = f"放量上涨（量+{vol_change:.0f}%），主力资金进场信号"
    elif price_change > 0 and vol_change < -10:
        signal = "weak_rally"
        desc = f"缩量上涨（量{vol_change:.0f}%），上涨乏力，可能回调"
    elif price_change < -1 and vol_change > 30:
        signal = "panic_selling"
        desc = f"放量下跌（量+{vol_change:.0f}%），恐慌抛售，注意风险"
    elif price_change < -1 and vol_change < -10:
        signal = "weak_selling"
        desc = f"缩量下跌（量{vol_change:.0f}%），惜售情绪，下跌空间有限"
    else:
        signal = "normal"
        desc = "量价关系正常"

    return {
        "signal": signal,
        "price_change": round(price_change, 2),
        "vol_change": round(vol_change, 1),
        "desc": desc,
    }


# ── 内部计算函数 ──


def _calc_momentum(prices: list[float], period: int = 5) -> float:
    """计算动量：最近period根K线的涨跌幅"""
    if len(prices) < period:
        return 0
    return round((prices[-1] / prices[-period] - 1) * 100, 2)


def _volume_anomaly(volumes: list[float]) -> dict | None:
    """成交量异动检测"""
    if len(volumes) < 10:
        return None
    avg = sum(volumes[:-1]) / max(len(volumes) - 1, 1) if len(volumes) > 1 else 1
    if avg <= 0:
        return None
    latest = volumes[-1]
    ratio = latest / avg
    if ratio > 2.5:
        return {
            "type": "volume",
            "direction": "up",
            "strength": round(ratio, 1),
            "desc": f"成交量放大{ratio:.1f}倍，异常活跃",
        }
    elif ratio < 0.3:
        return {
            "type": "volume",
            "direction": "down",
            "strength": round(ratio, 1),
            "desc": f"成交量萎缩至{ratio:.1f}倍，交投清淡",
        }
    return None


def _calc_atr(
    highs: list[float], lows: list[float], closes: list[float], period: int = 10
) -> float:
    """计算平均真实波幅（ATR）"""
    if len(highs) < period + 1:
        return 0
    tr_values = []
    for i in range(1, len(highs)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        tr_values.append(tr)
    return round(sum(tr_values[-period:]) / period, 4)


def _breakout_probability(
    closes: list[float], support: float, resistance: float
) -> dict:
    """突破概率计算"""
    current = closes[-1]
    range_width = resistance - support
    if range_width <= 0:
        return {
            "type": "breakout",
            "direction": "neutral",
            "probability": 0,
            "desc": "无有效支撑阻力区间",
        }

    # 价格在区间中的位置
    position = (current - support) / range_width
    # 距上/下沿的距离
    dist_to_resistance = (resistance - current) / current * 100
    dist_to_support = (current - support) / current * 100

    # 距突破点越近概率越高，但也可能反转
    if dist_to_resistance < 1:
        return {
            "type": "breakout",
            "direction": "up_target",
            "probability": round(100 - dist_to_resistance * 50, 1),
            "desc": f"接近阻力位{resistance}（距{dist_to_resistance:.1f}%），突破概率较高",
        }
    if dist_to_support < 1:
        return {
            "type": "breakout",
            "direction": "down_target",
            "probability": round(100 - dist_to_support * 50, 1),
            "desc": f"接近支撑位{support}（距{dist_to_support:.1f}%），跌破概率较高",
        }

    return {
        "type": "breakout",
        "direction": "neutral",
        "probability": round(max(0, 50 - position * 60), 1),
        "desc": "价格处于区间中部，短期突破概率较低",
    }
