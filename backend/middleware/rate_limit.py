"""简单的基于内存的 IP 速率限制中间件"""

import logging
import time
from collections import defaultdict

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# 默认配置：每 IP 每分钟 30 次请求
DEFAULT_RATE_LIMIT = 30
DEFAULT_WINDOW_SECONDS = 60
# 内存表上限：防恶意分布式来源导致的无限增长（超限时整体清空重建）
_MAX_CLIENTS = 10000


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        max_requests: int = DEFAULT_RATE_LIMIT,
        window_seconds: int = DEFAULT_WINDOW_SECONDS,
    ):
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._store: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()

        # 内存表规模防御：来源数超过阈值时整体重置（清空过期数据的同时防止 OOM）
        if len(self._store) > _MAX_CLIENTS:
            self._store.clear()
            logger.warning("速率限制存储已重置（来源数超限: %d）", _MAX_CLIENTS)

        # 惰性清理：仅在来源已有记录时修剪过期时间戳（避免为海量新来源做 O(n) 遍历）
        window_start = now - self.window_seconds
        if client_ip in self._store:
            ts = self._store[client_ip]
            if ts and ts[-1] > window_start:
                # 窗口内最近一次请求没过期时才做修剪；否则直接保留并判断
                self._store[client_ip] = [t for t in ts if t > window_start]
            else:
                # 最近请求已过期，清空该来源旧记录
                self._store[client_ip] = []

        if len(self._store[client_ip]) >= self.max_requests:
            retry_after = int(self._store[client_ip][0] + self.window_seconds - now) + 1
            logger.warning(
                "速率限制触发: IP=%s, 请求数=%d", client_ip, len(self._store[client_ip])
            )
            raise HTTPException(
                status_code=429,
                detail=f"请求过于频繁，请 {retry_after} 秒后重试",
                headers={"Retry-After": str(retry_after)},
            )

        self._store[client_ip].append(now)
        return await call_next(request)
