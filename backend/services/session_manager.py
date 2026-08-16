"""会话管理服务 - 追踪活跃会话，支持 SSE 消息推送

- 记录当前活跃的 conversation session
- 提供消息队列，支持系统消息推送
"""
import asyncio
import logging
from typing import Dict, Optional, List

logger = logging.getLogger(__name__)

# ── 全局状态 ──
active_session_id: Optional[str] = None
# session_id -> list of queued messages
_message_queues: Dict[str, list] = {}


def set_active_session(session_id: str):
    """设置当前活跃会话"""
    global active_session_id
    active_session_id = session_id
    logger.debug(f"活跃会话设置为: {session_id}")


def get_active_session() -> Optional[str]:
    """获取当前活跃会话 ID"""
    return active_session_id


def push_system_message(session_id: str, message: dict):
    """向指定会话推送系统消息

    Args:
        session_id: 目标会话 ID
        message: 消息 dict，如 {"type": "monitor_alert", "data": {...}}
    """
    if session_id not in _message_queues:
        _message_queues[session_id] = []
    _message_queues[session_id].append(message)
    # 限制队列长度，防止内存泄漏
    if len(_message_queues[session_id]) > 100:
        _message_queues[session_id] = _message_queues[session_id][-100:]


def poll_messages(session_id: str) -> List[dict]:
    """获取并清空指定会话的排队消息

    Args:
        session_id: 目标会话 ID

    Returns:
        消息列表，返回后清空队列
    """
    messages = _message_queues.pop(session_id, [])
    return messages
