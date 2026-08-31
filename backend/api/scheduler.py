"""定时任务调度 API

让 Agent 与用户能够制定、查看、暂停、删除、立即执行定时任务。
调度循环在服务层（services/scheduler.py）由后台任务驱动。
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..middleware.error_handler import get_current_user
from ..services import db
from ..services.scheduler import _compute_next_run, run_task_now

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])


class ScheduleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    agent_key: str = "chief_strategist"
    prompt: str = Field(..., min_length=1)
    schedule_type: str = "interval"  # interval | daily
    interval_seconds: int = Field(3600, ge=10)
    daily_time: str | None = None  # HH:MM


class ScheduleUpdate(BaseModel):
    name: str | None = None
    prompt: str | None = None
    schedule_type: str | None = None
    interval_seconds: int | None = Field(None, ge=10)
    daily_time: str | None = None
    status: str | None = None  # active | paused


@router.get("/tasks")
async def list_tasks(user_id: str = Depends(get_current_user)):
    tasks = await db.list_scheduled_tasks(user_id)
    return {"tasks": tasks}


@router.post("/tasks")
async def create_task(req: ScheduleCreate, user_id: str = Depends(get_current_user)):
    if req.schedule_type not in ("interval", "daily"):
        raise HTTPException(400, "schedule_type must be interval or daily")
    next_run = _compute_next_run(
        {"schedule_type": req.schedule_type, "interval_seconds": req.interval_seconds,
         "daily_time": req.daily_time},
        datetime.now(),
    )
    task = await db.create_scheduled_task(
        user_id, req.name, req.agent_key, req.prompt,
        schedule_type=req.schedule_type, interval_seconds=req.interval_seconds,
        daily_time=req.daily_time, next_run_at=next_run,
    )
    return {"success": True, "task": task}


@router.put("/tasks/{task_id}")
async def update_task(task_id: int, req: ScheduleUpdate, user_id: str = Depends(get_current_user)):
    existing = await db.get_scheduled_task(task_id, user_id)
    if not existing:
        raise HTTPException(404, "任务不存在")

    fields = req.model_dump(exclude_unset=True)
    if fields.get("schedule_type") and fields["schedule_type"] not in ("interval", "daily"):
        raise HTTPException(400, "schedule_type must be interval or daily")
    if fields.get("status") and fields["status"] not in ("active", "paused"):
        raise HTTPException(400, "status must be active or paused")

    # 若调度参数变化，重算下一次运行时间
    if "schedule_type" in fields or "interval_seconds" in fields or "daily_time" in fields:
        merged = {
            "schedule_type": fields.get("schedule_type", existing.get("schedule_type")),
            "interval_seconds": fields.get("interval_seconds", existing.get("interval_seconds")),
            "daily_time": fields.get("daily_time", existing.get("daily_time")),
        }
        fields["next_run_at"] = _compute_next_run(merged, datetime.now())

    ok = await db.update_scheduled_task(task_id, user_id, **fields)
    return {"success": ok}


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: int, user_id: str = Depends(get_current_user)):
    ok = await db.delete_scheduled_task(task_id, user_id)
    if not ok:
        raise HTTPException(404, "任务不存在")
    return {"success": True}


@router.post("/tasks/{task_id}/run")
async def run_task(task_id: int, user_id: str = Depends(get_current_user)):
    result = await run_task_now(task_id, user_id)
    if not result.get("success"):
        raise HTTPException(404, result.get("message", "任务不存在"))
    return result
