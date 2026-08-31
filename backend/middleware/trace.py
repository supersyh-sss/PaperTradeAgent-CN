"""Trace ID 中间件 — 全链路追踪

为每个 HTTP 请求生成唯一 trace_id，通过 ContextVar 在线程/协程间传递，
确保同一请求的所有日志自动关联到同一 trace_id。
"""
import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from ..services.logging_config import generate_trace_id, set_trace_id

logger = logging.getLogger(__name__)


class TraceMiddleware(BaseHTTPMiddleware):
    """Trace ID 中间件

    · 从 X-Trace-ID 请求头提取 trace_id（如果有），否则生成新的
    · 设置 ContextVar trace_id
    · 将 X-Trace-ID 写入响应头
    · 记录请求开始和结束日志（含 method、path、status_code、duration）
    """

    async def dispatch(self, request: Request, call_next):
        # 1. 提取或生成 trace_id
        trace_id = request.headers.get("X-Trace-ID")
        if not trace_id:
            trace_id = generate_trace_id()
        set_trace_id(trace_id)

        # 2. 请求开始日志
        start_time = time.time()
        logger.info(
            ">>> request_start",
            extra={
                "extra_fields": {
                    "method": request.method,
                    "path": request.url.path,
                    "trace_id": trace_id,
                }
            },
        )

        # 3. 执行请求
        response: Response = await call_next(request)

        # 4. 请求结束日志
        duration_ms = round((time.time() - start_time) * 1000, 2)
        status_code = response.status_code
        log_level = logging.WARNING if status_code >= 400 else logging.INFO
        logger.log(
            log_level,
            "<<< request_end",
            extra={
                "extra_fields": {
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                    "trace_id": trace_id,
                }
            },
        )

        # 5. 将 trace_id 写入响应头
        response.headers["X-Trace-ID"] = trace_id

        return response
