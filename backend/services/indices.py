"""大盘指数数据服务 - 上证/深证/创业板/科创50/沪深300"""
from datetime import datetime, timedelta, timezone

BJT = timezone(timedelta(hours=8))

# 主要A股指数编码
TRACKED_INDICES = {
    "sh000001": "上证指数",
    "sz399001": "深证成指",
    "sz399006": "创业板指",
    "sh000688": "科创50",
    "sh000300": "沪深300",
    "sh000016": "上证50",
}

# 板块涨幅排名（用于市场情绪）
SECTOR_INDICES = {
    "sh880471": "银行业",
    "sh880472": "证券业",
    "sh880473": "保险业",
    "sz399986": "银行指数",
}

# 全局指数缓存
_index_cache: dict[str, dict] = {}
_index_cache_ts: str | None = None  # 指数缓存最后更新时间（北京时间 ISO）


def get_cached_indices() -> dict:
    """获取缓存的大盘指数"""
    return dict(_index_cache)


def get_cached_indices_time() -> str | None:
    """获取指数缓存最后更新时间（供 Agent 时间感知）"""
    return _index_cache_ts


def get_cached_index(symbol: str) -> dict | None:
    """获取单个指数"""
    return _index_cache.get(symbol)


def get_market_sentiment() -> dict:
    """从指数数据计算市场情绪"""
    if not _index_cache:
        return {"sentiment": "unknown", "score": 0, "detail": "无数据"}

    up_count = 0
    total_count = 0
    total_change = 0.0
    details = []

    for sym, name in TRACKED_INDICES.items():
        data = _index_cache.get(sym)
        if data:
            total_count += 1
            change_pct = data.get("change_pct", 0)
            total_change += change_pct
            if change_pct > 0:
                up_count += 1
            details.append(f"{name}: {change_pct:+.2f}%")

    if total_count == 0:
        return {"sentiment": "unknown", "score": 0, "detail": "无数据"}

    avg_change = total_change / total_count
    up_ratio = up_count / total_count

    if avg_change > 1.5:
        sentiment = "strong_bullish"
    elif avg_change > 0.3:
        sentiment = "bullish"
    elif avg_change > -0.3:
        sentiment = "neutral"
    elif avg_change > -1.5:
        sentiment = "bearish"
    else:
        sentiment = "strong_bearish"

    return {
        "sentiment": sentiment,
        "score": round(avg_change, 2),
        "up_ratio": round(up_ratio, 2),
        "index_count": total_count,
        "detail": "; ".join(details),
    }


def update_index_cache(data: dict):
    """更新指数缓存"""
    global _index_cache_ts
    _index_cache.update(data)
    _index_cache_ts = datetime.now(BJT).isoformat()
