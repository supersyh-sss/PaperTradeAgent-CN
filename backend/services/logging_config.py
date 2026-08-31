"""结构化日志 & 链路追踪

用法：
  from .logging_config import get_logger, generate_trace_id, set_trace_id

  logger = get_logger(__name__)
  logger.info("处理请求", extra={"trace_id": trace_id})

trace_id 通过 contextvars 在线程/协程间传递，无需显式传参。
"""
import json
import logging
import sys
import uuid
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone

BJT = timezone(timedelta(hours=8))

# ContextVar 用于全链路 trace_id 传递
_trace_id: ContextVar[str] = ContextVar("trace_id", default="")
_request_id: ContextVar[str] = ContextVar("request_id", default="")


def generate_trace_id() -> str:
    """生成唯一追踪 ID"""
    return uuid.uuid4().hex[:12]


def set_trace_id(tid: str):
    """设置当前协程的 trace_id"""
    _trace_id.set(tid)


def get_trace_id() -> str:
    """获取当前协程的 trace_id"""
    return _trace_id.get()


def set_request_id(rid: str):
    _request_id.set(rid)


def get_request_id() -> str:
    return _request_id.get()


class JsonFormatter(logging.Formatter):
    """JSON 格式日志，包含 trace_id 和时间戳"""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "ts": datetime.now(BJT).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "trace_id": get_trace_id() or "-",
            "request_id": get_request_id() or "-",
        }
        if record.exc_info and record.exc_info[1]:
            log_entry["error"] = str(record.exc_info[1])
            log_entry["error_type"] = type(record.exc_info[1]).__name__

        # 合并 extra 字段
        extra_fields = getattr(record, "extra_fields", None)
        if extra_fields:
            log_entry.update(extra_fields)

        return json.dumps(log_entry, ensure_ascii=False, default=str)


def get_logger(name: str) -> logging.Logger:
    """获取带上下文的 logger"""
    logger = logging.getLogger(name)
    if not logger.handlers:
        # 只在首次调用时配置 handler（避免重复）
        return logger
    return logger


def configure_root_logger(level: int = logging.INFO, json_format: bool = True):
    """配置根 logger（应用启动时调用一次）"""
    root = logging.getLogger()
    root.setLevel(level)

    # 清除已有 handlers
    root.handlers.clear()

    if json_format:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
    else:
        # 开发环境使用可读格式
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        ))
        root.addHandler(handler)


# 便捷函数：异常时记录 log
def log_exception(logger: logging.Logger, msg: str, exc: Exception, level: str = "error"):
    """统一异常记录格式"""
    log_func = getattr(logger, level, logger.error)
    log_func(f"{msg}: {type(exc).__name__}: {exc}", exc_info=True)
