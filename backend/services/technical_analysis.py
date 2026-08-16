"""技术分析引擎：MA/RSI/MACD/布林带"""
import numpy as np
import pandas as pd
from typing import List, Dict, Optional


class TechnicalAnalyzer:
    """技术指标计算和交易信号生成"""

    @staticmethod
    def compute_ma(data: List[float], period: int) -> pd.Series:
        s = pd.Series(data)
        return s.rolling(window=period, min_periods=1).mean()

    @staticmethod
    def compute_ema(data: pd.Series, period: int) -> pd.Series:
        return data.ewm(span=period, adjust=False).mean()

    @staticmethod
    def compute_rsi(closes: pd.Series, period: int = 14) -> pd.Series:
        delta = closes.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    @staticmethod
    def compute_macd(closes: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
        ema_fast = closes.ewm(span=fast, adjust=False).mean()
        ema_slow = closes.ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line
        return {
            "macd": macd_line.tolist(),
            "signal": signal_line.tolist(),
            "histogram": histogram.tolist(),
        }

    @staticmethod
    def compute_bollinger(closes: pd.Series, period: int = 20, std_mult: float = 2.0) -> dict:
        middle = closes.rolling(window=period, min_periods=1).mean()
        std = closes.rolling(window=period, min_periods=1).std()
        upper = middle + std_mult * std
        lower = middle - std_mult * std
        return {
            "middle": middle.tolist(),
            "upper": upper.tolist(),
            "lower": lower.tolist(),
            "std": std.tolist(),
        }

    def analyze(self, kline_data: List[dict]) -> Optional[dict]:
        """
        对K线数据进行全面技术分析，生成结构化信号
        kline_data: [{"date": "...", "open": ..., "close": ..., "high": ..., "low": ..., "volume": ...}, ...]
        """
        if not kline_data or len(kline_data) < 20:
            return None

        df = pd.DataFrame(kline_data)
        closes = df["close"].astype(float)
        highs = df["high"].astype(float)
        lows = df["low"].astype(float)
        volumes = df["volume"].astype(float)

        # 计算均线
        ma5 = self.compute_ma(closes, 5)
        ma10 = self.compute_ma(closes, 10)
        ma20 = self.compute_ma(closes, 20)
        ma60 = self.compute_ma(closes, 60) if len(df) >= 60 else ma20

        # 计算RSI
        rsi = self.compute_rsi(closes, 14)

        # 计算MACD
        macd_data = self.compute_macd(closes, 12, 26, 9)

        # 计算布林带
        boll = self.compute_bollinger(closes, 20, 2.0)

        # 最新值
        latest_close = float(closes.iloc[-1])
        latest_ma5 = float(ma5.iloc[-1])
        latest_ma10 = float(ma10.iloc[-1])
        latest_ma20 = float(ma20.iloc[-1])
        latest_ma60 = float(ma60.iloc[-1])
        latest_rsi = float(rsi.iloc[-1])
        latest_macd_hist = float(macd_data["histogram"][-1])
        prev_macd_hist = float(macd_data["histogram"][-2]) if len(macd_data["histogram"]) > 1 else 0
        latest_bb_upper = float(boll["upper"][-1])
        latest_bb_lower = float(boll["lower"][-1])
        latest_bb_middle = float(boll["middle"][-1])
        latest_bb_std = float(boll["std"][-1])

        # 生成信号
        signals = []

        # MA信号
        if latest_close > latest_ma5 > latest_ma10 > latest_ma20:
            signals.append({"type": "MA", "signal": "多头排列", "strength": "strong"})
        elif latest_close < latest_ma5 < latest_ma10 < latest_ma20:
            signals.append({"type": "MA", "signal": "空头排列", "strength": "strong"})
        elif latest_close > latest_ma20:
            signals.append({"type": "MA", "signal": "站上20日均线", "strength": "moderate"})
        else:
            signals.append({"type": "MA", "signal": "跌破20日均线", "strength": "moderate"})

        # RSI信号
        if latest_rsi > 70:
            signals.append({"type": "RSI", "signal": "超买", "value": round(latest_rsi, 1), "strength": "warning"})
        elif latest_rsi < 30:
            signals.append({"type": "RSI", "signal": "超卖", "value": round(latest_rsi, 1), "strength": "opportunity"})
        elif latest_rsi > 50:
            signals.append({"type": "RSI", "signal": "偏强", "value": round(latest_rsi, 1), "strength": "normal"})
        else:
            signals.append({"type": "RSI", "signal": "偏弱", "value": round(latest_rsi, 1), "strength": "normal"})

        # 布林带信号
        if latest_close > latest_bb_upper:
            signals.append({"type": "BOLL", "signal": "突破上轨", "strength": "warning"})
        elif latest_close < latest_bb_lower:
            signals.append({"type": "BOLL", "signal": "跌破下轨", "strength": "opportunity"})

        # MACD信号
        macd_bullish = latest_macd_hist > 0 and prev_macd_hist <= 0
        macd_bearish = latest_macd_hist < 0 and prev_macd_hist >= 0
        if macd_bullish:
            signals.append({"type": "MACD", "signal": "金叉", "strength": "opportunity"})
        elif macd_bearish:
            signals.append({"type": "MACD", "signal": "死叉", "strength": "warning"})

        # 趋势判断
        if latest_close > latest_ma20 and latest_ma5 > latest_ma20:
            trend = "bullish"
        elif latest_close < latest_ma20 and latest_ma5 < latest_ma20:
            trend = "bearish"
        else:
            trend = "neutral"

        # 波动率
        volatility = round(latest_bb_std / latest_bb_middle * 100, 2) if latest_bb_middle else 0

        # 支撑/阻力
        recent_lows = sorted(lows.iloc[-20:].tolist())[:3]
        recent_highs = sorted(highs.iloc[-20:].tolist(), reverse=True)[:3]
        support_levels = [round(x, 2) for x in recent_lows]
        resistance_levels = [round(x, 2) for x in recent_highs]

        return {
            "trend": trend,
            "latest_price": round(latest_close, 2),
            "ma": {
                "ma5": round(latest_ma5, 2),
                "ma10": round(latest_ma10, 2),
                "ma20": round(latest_ma20, 2),
                "ma60": round(latest_ma60, 2),
            },
            "rsi": round(latest_rsi, 1),
            "macd_signal": "bullish" if latest_macd_hist > 0 else "bearish",
            "signals": signals,
            "bollinger": {
                "upper": round(latest_bb_upper, 2),
                "middle": round(latest_bb_middle, 2),
                "lower": round(latest_bb_lower, 2),
            },
            "support": support_levels,
            "resistance": resistance_levels,
            "volatility": volatility,
            "data_points": len(kline_data),
        }

    def to_markdown(self, analysis: dict, name: str = "") -> str:
        """将分析结果转换为Markdown格式"""
        if not analysis:
            return "暂无足够数据进行分析"

        lines = [f"## {name} 技术分析报告\n" if name else "## 技术分析报告\n"]
        lines.append(f"**最新价**: {analysis['latest_price']} | **趋势**: {analysis['trend']} | **RSI**: {analysis['rsi']}\n")

        lines.append("### 均线系统")
        ma = analysis['ma']
        lines.append(f"- MA5: {ma['ma5']} | MA10: {ma['ma10']} | MA20: {ma['ma20']} | MA60: {ma['ma60']}")

        lines.append("\n### 技术信号")
        for s in analysis['signals']:
            label_map = {"warning": "[风险]", "opportunity": "[机会]", "strong": "[强]", "moderate": "[中]", "normal": "[正常]"}
            label = label_map.get(s['strength'], '')
            lines.append(f"- {label} [{s['type']}] {s['signal']} (强度: {s['strength']})")

        lines.append(f"\n### 布林带")
        bb = analysis['bollinger']
        lines.append(f"- 上轨: {bb['upper']} | 中轨: {bb['middle']} | 下轨: {bb['lower']}")
        lines.append(f"- 波动率: {analysis['volatility']}%")

        lines.append(f"\n### 支撑/阻力")
        lines.append(f"- 支撑位: {', '.join(str(x) for x in analysis['support'])}")
        lines.append(f"- 阻力位: {', '.join(str(x) for x in analysis['resistance'])}")

        return "\n".join(lines)


# 全局实例
technical_analyzer = TechnicalAnalyzer()
