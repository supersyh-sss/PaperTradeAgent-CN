"""轻量级专业量化信号引擎（无需重型硬件，纯 numpy/pandas 即可运转）。

在原有 MA/RSI/MACD/布林带基础上，补齐 ATR/KDJ/ROC/Williams%R/CCI/OBV/ADX
等专业指标，并用「多信号加权评分」做集成，降低单指标噪声、提升预测稳定性。
"""

import numpy as np
import pandas as pd

logger = __import__("logging").getLogger(__name__)


def _series(values) -> pd.Series:
    return pd.Series([float(v) for v in values])


def compute_atr(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> float:
    """平均真实波幅 ATR：度量波动，用于止损/止盈与目标价区间。"""
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    return float(atr.iloc[-1]) if len(atr) else 0.0


def compute_kdj(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    n: int = 9,
    k_period: int = 3,
    d_period: int = 3,
) -> dict:
    """随机指标 KDJ：短线超买超卖动量。"""
    low_n = low.rolling(window=n, min_periods=1).min()
    high_n = high.rolling(window=n, min_periods=1).max()
    rsv = (close - low_n) / (high_n - low_n).replace(0, np.nan) * 100
    rsv = rsv.fillna(50.0)
    k = rsv.ewm(alpha=1 / k_period, adjust=False).mean()
    d = k.ewm(alpha=1 / d_period, adjust=False).mean()
    j = 3 * k - 2 * d
    return {
        "k": round(float(k.iloc[-1]), 2),
        "d": round(float(d.iloc[-1]), 2),
        "j": round(float(j.iloc[-1]), 2),
        "signal": "超买"
        if j.iloc[-1] > 80
        else ("超卖" if j.iloc[-1] < 20 else "中性"),
    }


def compute_roc(close: pd.Series, period: int = 12) -> float:
    """变动率 ROC：动量强度。"""
    if len(close) <= period:
        return 0.0
    return round(float((close.iloc[-1] / close.iloc[-1 - period] - 1) * 100), 2)


def compute_williams_r(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> float:
    """威廉指标 %R：-20 以上超买，-80 以下超卖。"""
    hh = high.rolling(window=period, min_periods=1).max()
    ll = low.rolling(window=period, min_periods=1).min()
    wr = (hh - close) / (hh - ll).replace(0, np.nan) * -100
    return round(float(wr.fillna(-50).iloc[-1]), 2)


def compute_cci(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 20
) -> float:
    """顺势指标 CCI：±100 为阈值。"""
    tp = (high + low + close) / 3
    ma = tp.rolling(window=period, min_periods=1).mean()
    md = tp.rolling(window=period, min_periods=1).apply(
        lambda x: np.mean(np.abs(x - x.mean())), raw=True
    )
    cci = (tp - ma) / (0.015 * md.replace(0, np.nan))
    return round(float(cci.fillna(0).iloc[-1]), 2)


def compute_obv(close: pd.Series, volume: pd.Series) -> str:
    """能量潮 OBV 趋势：量价配合方向。"""
    direction = np.sign(close.diff().fillna(0))
    obv = (direction * volume).cumsum()
    if len(obv) < 5:
        return "neutral"
    recent = obv.iloc[-5:]
    if recent.iloc[-1] > recent.iloc[0] and close.iloc[-1] > close.iloc[-5]:
        return "bullish"
    if recent.iloc[-1] < recent.iloc[0] and close.iloc[-1] < close.iloc[-5]:
        return "bearish"
    return "neutral"


def compute_adx(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> float:
    """平均趋向指数 ADX：趋势强度（>25 视为有趋势）。"""
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(
        np.where((down > up) & (down > 0), down, 0.0), index=high.index
    )
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = (
        100
        * plus_dm.ewm(alpha=1 / period, adjust=False).mean()
        / atr.replace(0, np.nan)
    )
    minus_di = (
        100
        * minus_dm.ewm(alpha=1 / period, adjust=False).mean()
        / atr.replace(0, np.nan)
    )
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1 / period, adjust=False).mean()
    return round(float(adx.fillna(0).iloc[-1]), 2)


def compute_volume_trend(volume: pd.Series, period: int = 20) -> float:
    """量能趋势：当前量相对过去均量的倍数。"""
    if len(volume) < period:
        return 1.0
    avg = volume.iloc[-period:-1].mean()
    return round(float(volume.iloc[-1] / avg), 2) if avg > 0 else 1.0


def compute_mfi(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    period: int = 14,
) -> float:
    """资金流量指标 MFI：量价结合的超买超卖资金流（>80 超买，<20 超卖）。"""
    tp = (high + low + close) / 3
    raw_flow = tp * volume
    pos_flow = raw_flow.where(tp > tp.shift(1), 0.0)
    neg_flow = raw_flow.where(tp < tp.shift(1), 0.0)
    pos_sum = pos_flow.rolling(window=period, min_periods=1).sum()
    neg_sum = neg_flow.rolling(window=period, min_periods=1).sum()
    ratio = pos_sum / neg_sum.replace(0, np.nan)
    mfi = 100 - (100 / (1 + ratio))
    return round(float(mfi.fillna(50.0).iloc[-1]), 2)


def compute_bias(close: pd.Series, period: int = 6) -> float:
    """乖离率 BIAS：价格相对均线的偏离程度（回归均值信号）。"""
    ma = close.rolling(window=period, min_periods=1).mean()
    if not len(ma) or ma.iloc[-1] == 0:
        return 0.0
    return round(float((close.iloc[-1] / ma.iloc[-1] - 1) * 100), 2)


def compute_psy(close: pd.Series, period: int = 12) -> float:
    """心理线 PSY：近 N 期上涨天数占比（市场情绪/超买超卖）。"""
    ups = (close.diff() > 0).astype(int)
    psy = ups.rolling(window=period, min_periods=1).mean() * 100
    return round(float(psy.iloc[-1]), 2)


def detect_regime(close: pd.Series, adx: float, period: int = 20) -> str:
    """市场状态识别：趋势（trend）或震荡（range），用于动态调整信号权重。

    依据 ADX 强度与价格相对均线的斜率：ADX >= 25 且均线斜率明显 → trend，
    否则 → range（震荡，均值回归类信号更有效）。
    """
    if len(close) < period + 1:
        return "range"
    ma = close.rolling(window=period, min_periods=1).mean()
    slope = float(ma.iloc[-1] - ma.iloc[0]) / float(ma.iloc[0]) if ma.iloc[0] else 0.0
    if adx >= 25 and abs(slope) > 0.005:
        return "trend"
    return "range"


def compute_extended_indicators(kline_data: list[dict]) -> dict | None:
    """对 K 线计算扩展指标，返回结构化结果（供技术面合并与评分）。"""
    if not kline_data or len(kline_data) < 20:
        return None
    df = pd.DataFrame(kline_data)
    close = _series(df["close"])
    high = _series(df["high"])
    low = _series(df["low"])
    volume = (
        _series(df["volume"])
        if "volume" in df and df["volume"].notna().any()
        else _series([0] * len(df))
    )

    atr = compute_atr(high, low, close)
    latest_close = float(close.iloc[-1])
    atr_pct = round(atr / latest_close * 100, 2) if latest_close else 0.0
    adx = compute_adx(high, low, close)

    return {
        "atr": round(atr, 3),
        "atr_pct": atr_pct,
        "kdj": compute_kdj(high, low, close),
        "roc": compute_roc(close),
        "williams_r": compute_williams_r(high, low, close),
        "cci": compute_cci(high, low, close),
        "obv_trend": compute_obv(close, volume),
        "adx": adx,
        "volume_trend": compute_volume_trend(volume),
        "mfi": compute_mfi(high, low, close, volume),
        "bias": compute_bias(close),
        "psy": compute_psy(close),
        "regime": detect_regime(close, adx),
    }


def compute_quant_score(tech: dict, ext: dict) -> dict:
    """多信号加权集成评分（0-100，越高越偏多），降低单指标噪声。

    返回总分 + 各分项，便于 Agent 解释判断依据。
    """
    score = 50.0  # 中性起点
    parts = {}

    # 趋势
    trend = tech.get("trend", "neutral")
    if trend == "bullish":
        score += 15
        parts["trend"] = +15
    elif trend == "bearish":
        score -= 15
        parts["trend"] = -15
    else:
        parts["trend"] = 0

    # RSI：50 附近健康，>70 超买、<30 超卖（反向）
    rsi = float(tech.get("rsi", 50) or 50)
    if 40 <= rsi <= 60:
        parts["rsi"] = 0
    elif rsi > 70:
        score -= 10
        parts["rsi"] = -10
    elif rsi < 30:
        score += 10
        parts["rsi"] = +10
    else:
        score += (60 - rsi) * 0.2  # 略偏高→谨慎，略偏低→机会
        parts["rsi"] = round((60 - rsi) * 0.2, 1)

    # MACD
    if tech.get("macd_signal") == "bullish":
        score += 10
        parts["macd"] = +10
    elif tech.get("macd_signal") == "bearish":
        score -= 10
        parts["macd"] = -10
    else:
        parts["macd"] = 0

    # 布林带位置
    bb = tech.get("bollinger", {})
    price = float(tech.get("latest_price", 0) or 0)
    if price and bb.get("upper") and bb.get("lower"):
        width = (bb["upper"] - bb["lower"]) or 1
        pos = (price - bb["lower"]) / width  # 0 下轨 ~ 1 上轨
        if pos < 0.2:
            score += 8
            parts["boll"] = +8
        elif pos > 0.8:
            score -= 8
            parts["boll"] = -8
        else:
            parts["boll"] = 0

    # 扩展指标（若可用）
    if ext:
        kdj_j = ext.get("kdj", {}).get("j", 50)
        if kdj_j < 20:
            score += 6
            parts["kdj"] = +6
        elif kdj_j > 80:
            score -= 6
            parts["kdj"] = -6
        else:
            parts["kdj"] = 0

        cci = float(ext.get("cci", 0) or 0)
        if cci < -100:
            score += 5
            parts["cci"] = +5
        elif cci > 100:
            score -= 5
            parts["cci"] = -5
        else:
            parts["cci"] = 0

        adx = float(ext.get("adx", 0) or 0)
        if adx >= 25:
            score += 4
            parts["adx"] = +4
        else:
            parts["adx"] = 0

        obv = ext.get("obv_trend", "neutral")
        if obv == "bullish":
            score += 4
            parts["obv"] = +4
        elif obv == "bearish":
            score -= 4
            parts["obv"] = -4
        else:
            parts["obv"] = 0

        vr = float(ext.get("volume_trend", 1) or 1)
        if vr > 1.5 and trend == "bullish":
            score += 4
            parts["volume"] = +4
        elif vr > 1.5 and trend == "bearish":
            score -= 4
            parts["volume"] = -4
        else:
            parts["volume"] = 0

        # 资金流量 MFI：>80 超买（资金过热，减分），<20 超卖（资金回流机会，加分）
        mfi = float(ext.get("mfi", 50) or 50)
        if mfi > 80:
            score -= 5
            parts["mfi"] = -5
        elif mfi < 20:
            score += 5
            parts["mfi"] = +5
        else:
            parts["mfi"] = 0

        # 乖离率 BIAS：偏离过大回归均值（< -6 超跌机会，> +6 超涨风险）
        bias = float(ext.get("bias", 0) or 0)
        if bias < -6:
            score += 4
            parts["bias"] = +4
        elif bias > 6:
            score -= 4
            parts["bias"] = -4
        else:
            parts["bias"] = 0

        # 心理线 PSY：<25 超卖情绪（机会），>75 超买情绪（风险）
        psy = float(ext.get("psy", 50) or 50)
        if psy < 25:
            score += 3
            parts["psy"] = +3
        elif psy > 75:
            score -= 3
            parts["psy"] = -3
        else:
            parts["psy"] = 0

        # 市场状态：震荡市均值回归信号更可信，趋势市趋势信号更可信
        parts["regime"] = ext.get("regime", "range")

    score = round(max(0, min(100, score)), 1)
    label = "偏多" if score >= 60 else ("偏空" if score <= 40 else "中性")
    return {
        "score": score,
        "label": label,
        "parts": parts,
        "regime": ext.get("regime", "range") if ext else "range",
    }
