"""多数据源管理器：自动故障切换 + 健康恢复"""
import logging
from enum import Enum
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from .cache import market_cache, api_limiter
from .tencent_api import tencent_api
from .sina_api import sina_api
from .symbol import pure_code, to_tencent_code
from .live_prices import add_hot_symbol

logger = logging.getLogger(__name__)


class DataSource(Enum):
    TENCENT = "tencent"
    SINA = "sina"
    CACHE = "cache"


class DataSourceManager:
    """多数据源管理器"""

    PRIORITY = [DataSource.TENCENT, DataSource.SINA]
    RECOVERY_INTERVAL = timedelta(minutes=5)

    def __init__(self):
        self.failed_sources: Dict[DataSource, datetime] = {}
        self.success_count: Dict[DataSource, int] = {s: 0 for s in DataSource}
        self.fail_count: Dict[DataSource, int] = {s: 0 for s in DataSource}

    async def get_realtime(self, codes: List[str]) -> Dict[str, dict]:
        """按优先级获取实时行情，自动故障切换。
        - 6 位纯代码股票返回 key 为纯代码
        - 已带 sh/sz/bj 前缀的代码（如指数）保持原 key
        """
        # 1. 分离普通股票代码与已带前缀的指数/ETF 代码
        raw_codes = [c.lower().strip() for c in codes if c and self._is_prefixed(c)]
        normalized = [pure_code(c) for c in codes if c and not self._is_prefixed(c)]

        # 注册热符号：用户查询的非指数股票自动加入实时轮询追踪
        for nc in normalized:
            add_hot_symbol(nc)

        result: Dict[str, dict] = {}

        # 2. 股票代码：检查缓存并补齐缺失
        if normalized:
            cached = market_cache.get_batch(normalized)
            missing = [c for c in normalized if not market_cache.get_price(c)]
            result.update(cached)

            if missing:
                for source in self.PRIORITY:
                    if self._is_source_down(source):
                        continue
                    try:
                        fetched = await self._fetch_realtime(source, missing)
                        if fetched:
                            normalized_result = {pure_code(k): v for k, v in fetched.items() if v}
                            market_cache.set_batch(normalized_result)
                            self.success_count[source] += 1
                            self._mark_source_healthy(source)
                            result.update(normalized_result)
                            missing = []
                            break
                    except Exception as e:
                        logger.warning(f"[{source.value}] 实时行情获取失败: {e}")
                        self._mark_source_failed(source)
                        self.fail_count[source] += 1

        # 3. 带前缀代码（指数等）：直接请求并映射回原 key
        if raw_codes:
            for source in self.PRIORITY:
                if self._is_source_down(source):
                    continue
                try:
                    fetched = await self._fetch_realtime(source, raw_codes)
                    if fetched:
                        for raw in raw_codes:
                            pure = pure_code(raw)
                            if pure in fetched:
                                result[raw] = fetched[pure]
                                result[raw]["symbol"] = raw
                        self.success_count[source] += 1
                        self._mark_source_healthy(source)
                        break
                except Exception as e:
                    logger.warning(f"[{source.value}] 指数行情获取失败: {e}")
                    self._mark_source_failed(source)
                    self.fail_count[source] += 1

        return result

    @staticmethod
    def _is_prefixed(code: str) -> bool:
        c = (code or "").strip().lower()
        return len(c) == 8 and c.startswith(("sh", "sz", "bj")) and c[2:].isdigit()

    async def get_kline(self, code: str, period: str = "day", count: int = 250) -> Optional[List[dict]]:
        """获取历史K线"""
        norm = pure_code(code)
        # 检查缓存
        cached = market_cache.get_kline(norm)
        if cached:
            return cached

        # 尝试腾讯
        try:
            result = await tencent_api.get_kline(norm, period, count)
            if result:
                market_cache.set_kline(norm, result)
                return result
        except Exception as e:
            logger.warning(f"[tencent] K线获取失败: {e}")

        return None

    async def get_realtime_single(self, code: str) -> Optional[dict]:
        """获取单只股票行情"""
        result = await self.get_realtime([code])
        return result.get(pure_code(code))

    async def _fetch_realtime(self, source: DataSource, codes: List[str]) -> Dict[str, dict]:
        if source == DataSource.TENCENT:
            return await tencent_api.get_realtime(codes)
        elif source == DataSource.SINA:
            return await sina_api.get_realtime(codes)
        return {}

    def _is_source_down(self, source: DataSource) -> bool:
        if source not in self.failed_sources:
            return False
        if datetime.now() - self.failed_sources[source] > self.RECOVERY_INTERVAL:
            del self.failed_sources[source]
            return False
        return True

    def _mark_source_failed(self, source: DataSource):
        self.failed_sources[source] = datetime.now()

    def _mark_source_healthy(self, source: DataSource):
        if source in self.failed_sources:
            del self.failed_sources[source]

    def get_source_status(self) -> dict:
        return {
            "tencent": {"healthy": DataSource.TENCENT not in self.failed_sources},
            "sina": {"healthy": DataSource.SINA not in self.failed_sources},
            "success_count": {k.value: v for k, v in self.success_count.items()},
        }


# 全局实例
data_source_manager = DataSourceManager()
