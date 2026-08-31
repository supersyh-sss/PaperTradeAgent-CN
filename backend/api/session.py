"""会话 SSE 端点 - 实时推送系统消息（监控告警、突发新闻等）"""
import asyncio
import json
import logging

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from ..services.live_prices import _get_order_queue
from ..services.session_manager import poll_messages, set_active_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/session", tags=["session"])


@router.get("/stream")
async def session_stream(session_id: str = Query(...)):
    """SSE 端点：实时推送系统消息（监控告警/突发新闻）

    连接后自动设置该 session 为活跃会话。
    事件格式：{"type": "system_alert"|"breaking_news"|"monitor_warning"|"monitor_alert", "data": {...}}
    """
    set_active_session(session_id)
    logger.info(f"会话 SSE 已连接: {session_id}")

    order_queue = _get_order_queue()

    async def event_generator():
        yield f"data: {json.dumps({'type': 'connected', 'session_id': session_id}, ensure_ascii=False)}\n\n"

        while True:
            try:
                # 先检查 session 专属消息
                msgs = poll_messages(session_id)
                for msg in msgs:
                    if isinstance(msg, str):
                        yield f"data: {msg}\n\n"
                    else:
                        yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"

                # 再检查全局订单/监控事件
                try:
                    event = order_queue.get_nowait()
                    yield f"data: {event}\n\n"
                except asyncio.QueueEmpty:
                    pass

                await asyncio.sleep(1)

            except asyncio.CancelledError:
                logger.info(f"会话 SSE 断开: {session_id}")
                break
            except Exception:
                logger.exception("会话 SSE 异常")
                await asyncio.sleep(3)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
