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


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_requests: int = DEFAULT_RATE_LIMIT, window_seconds: int = DEFAULT_WINDOW_SECONDS):
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._store: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()

        # 清理过期记录
        window_start = now - self.window_seconds
        timestamps = self._store[client_ip]
        self._store[client_ip] = [t for t in timestamps if t > window_start]

        if len(self._store[client_ip]) >= self.max_requests:
            retry_after = int(self._store[client_ip][0] + self.window_seconds - now) + 1
            logger.warning(f"速率限制触发: IP={client_ip}, 请求数={len(self._store[client_ip])}")
            raise HTTPException(
                status_code=429,
                detail=f"请求过于频繁，请 {retry_after} 秒后重试",
                headers={"Retry-After": str(retry_after)},
            )

        self._store[client_ip].append(now)
        return await call_next(request)
