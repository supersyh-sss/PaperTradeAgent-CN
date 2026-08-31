"""数据缓存与API限流"""

from aiolimiter import AsyncLimiter
from cachetools import TTLCache

from ..config import API_RATE_LIMIT, API_RATE_WINDOW, KLINE_CACHE_TTL, PRICE_CACHE_TTL


class MarketDataCache:
    """市场行情缓存层"""

    def __init__(self):
        # L1: 实时行情缓存，TTL可配置，最多200只
        self._price_cache: TTLCache = TTLCache(maxsize=200, ttl=PRICE_CACHE_TTL)
        # L2: 历史K线缓存，TTL可配置
        self._kline_cache: TTLCache = TTLCache(maxsize=50, ttl=KLINE_CACHE_TTL)

    def get_price(self, code: str) -> dict | None:
        return self._price_cache.get(code)

    def get_batch(self, codes: list[str]) -> dict[str, dict]:
        return {c: self._price_cache[c] for c in codes if c in self._price_cache}

    def set_batch(self, data: dict[str, dict]):
        self._price_cache.update(data)

    def get_kline(self, code: str) -> list[dict] | None:
        return self._kline_cache.get(code)

    def set_kline(self, code: str, data: list[dict]):
        self._kline_cache[code] = data


# 全局实例
market_cache = MarketDataCache()

# 全局限流器: 次数/分钟
api_limiter = AsyncLimiter(max_rate=API_RATE_LIMIT, time_period=API_RATE_WINDOW)
