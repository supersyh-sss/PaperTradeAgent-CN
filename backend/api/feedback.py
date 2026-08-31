"""消息反馈 API - 用户对 Agent 输出的点赞/倒赞（持久化）"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..middleware.error_handler import get_current_user
from ..services import db

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


def content_hash(agent: str, content: str) -> str:
    """与前端一致的 djb2 哈希，用于幂等标识一条 Agent 输出"""
    s = f"{agent}\n{content}"
    h = 5381
    for ch in s:
        h = (h * 33 + ord(ch)) & 0xFFFFFFFF
    return str(h)


class FeedbackRequest(BaseModel):
    agent: str
    content: str
    feedback: str  # up | down | none
    conversation_id: str | None = None


@router.post("")
async def submit_feedback(req: FeedbackRequest, user_id: str = Depends(get_current_user)):
    feedback = req.feedback.strip().lower()
    if feedback not in ("up", "down", "none"):
        raise HTTPException(400, "feedback 必须为 up / down / none")

    h = content_hash(req.agent, req.content)
    if feedback == "none":
        await db.delete_feedback(user_id, h)
        return {"success": True, "content_hash": h, "feedback": None}

    await db.upsert_feedback(user_id, req.agent, req.content, h, feedback, req.conversation_id)
    return {"success": True, "content_hash": h, "feedback": feedback}


@router.get("")
async def list_feedback(user_id: str = Depends(get_current_user)):
    items = await db.get_feedback(user_id)
    return {"data": items}
