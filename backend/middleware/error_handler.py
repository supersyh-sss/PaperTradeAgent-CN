"""认证中间件 + 全局异常处理"""

import logging

from fastapi import Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from ..config import TEST_TOKEN, TEST_USER_ID

logger = logging.getLogger(__name__)


def _extract_bearer_token(authorization: str) -> str:
    """从 Authorization header 提取 Bearer token"""
    if not authorization:
        return ""
    parts = authorization.strip().split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    # 兼容旧配置：如果 TEST_TOKEN 不带 Bearer 前缀，直接返回原始值
    return authorization.strip()


async def get_current_user(
    authorization: str = Header(None), token: str = Query(None)
) -> str:
    """获取当前用户（MVP Token 认证，兼容 header 与 query 传递）"""
    token_value = _extract_bearer_token(authorization) or _extract_bearer_token(token)
    expected = _extract_bearer_token(TEST_TOKEN)
    if not token_value or token_value != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing token")
    return TEST_USER_ID


async def global_exception_handler(request: Request, exc: Exception):
    """全局异常处理：对外隐藏内部实现细节"""
    exc_type = type(exc).__name__

    if isinstance(exc, HTTPException):
        status_code = exc.status_code
        message = str(exc.detail) if exc.detail else "请求错误"
    elif isinstance(exc, ValueError):
        status_code = 400
        message = str(exc)
    elif isinstance(exc, KeyError):
        status_code = 400
        message = f"缺少必要参数: {exc}"
    elif isinstance(exc, ConnectionError):
        status_code = 503
        message = "数据源连接失败，请稍后重试"
    elif isinstance(exc, TimeoutError):
        status_code = 504
        message = "数据请求超时，请稍后重试"
    else:
        status_code = 500
        message = "系统内部错误，请稍后重试"

    logger.error("[%s] %s", exc_type, exc)

    return JSONResponse(
        status_code=status_code,
        content={
            "error_code": exc_type,
            "message": message,
            # 仅对 4xx 暴露原始错误；5xx 不暴露实现细节
            "detail": str(exc) if status_code < 500 else None,
        },
    )
