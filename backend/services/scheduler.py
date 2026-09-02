"""定时任务调度服务

让系统内 Agent 能够制定并调度计划任务：
  - interval 类型：每 interval_seconds 秒执行一次
  - daily 类型：每天 daily_time（HH:MM）执行一次

调度循环为单后台 asyncio 任务，随 FastAPI lifespan 启停；执行失败不影响主链路。
"""

import asyncio
import logging
from datetime import datetime, timedelta

from . import db
from .trading_time import CHINA_TZ

logger = logging.getLogger(__name__)


# 调度统一使用北京时间 naive 时钟（服务器可能不在东八区）
def _now_bjt() -> datetime:
    return datetime.now(CHINA_TZ).replace(tzinfo=None)


# 调度循环轮询间隔（秒）
_POLL_INTERVAL_SECONDS = 15


def _compute_next_run(task: dict, from_time: datetime) -> str:
    """根据任务调度类型计算下一次执行时间（返回 ISO 字符串）。"""
    if task.get("schedule_type") == "daily":
        raw = (task.get("daily_time") or "").strip()
        try:
            hh, mm = raw.split(":")
            target = from_time.replace(
                hour=int(hh), minute=int(mm), second=0, microsecond=0
            )
            if target <= from_time:
                target += timedelta(days=1)
            return target.isoformat(timespec="seconds")
        except (ValueError, AttributeError):
            # daily_time 非法时回退为 24 小时
            return (from_time + timedelta(hours=24)).isoformat(timespec="seconds")

    interval = max(int(task.get("interval_seconds") or 3600), 10)
    return (from_time + timedelta(seconds=interval)).isoformat(timespec="seconds")


async def _execute_prompt(user_input: str, user_id: str) -> str:
    """复用 LangGraph 主链路执行一段提示，返回最终回复文本。"""
    from ..agents.graph import trading_graph
    from ..agents.state import create_initial_state

    state = create_initial_state(user_input, user_id)
    try:
        result = await trading_graph.ainvoke(state)
    except Exception as e:
        logger.warning("定时任务图执行失败: %s", e)
        return f"任务执行出错：{e}"

    needs_report = result.get("needs_report", False)

    if not needs_report:
        from ..api.chat import _generate_non_report_reply

        return await _generate_non_report_reply(result)

    from ..agents.prompts import RESPONSE_GENERATOR_SYSTEM
    from ..agents.response_generator import _build_context
    from ..services.llm import choose_client

    context = _build_context(result)
    messages = [
        {"role": "system", "content": RESPONSE_GENERATOR_SYSTEM},
        {"role": "user", "content": context},
    ]
    try:
        return await choose_client(True).chat(
            messages, temperature=0.5, max_tokens=3072
        )
    except Exception:
        logger.warning("定时任务报告生成失败，使用降级回复", exc_info=True)
        try:
            from ..agents.response_generator import _fallback_response

            return _fallback_response(result)
        except Exception:
            return "报告生成失败，请重试。"


async def run_task_now(task_id: int, user_id: str = "default") -> dict:
    """立即执行一个定时任务，并回写结果与下一次运行时间。"""
    task = await db.get_scheduled_task(task_id, user_id)
    if not task:
        return {"success": False, "message": "任务不存在"}

    now = _now_bjt()
    try:
        result = await _execute_prompt(task.get("prompt", ""), user_id)
    except Exception as e:
        logger.error("定时任务 %s 执行失败: %s", task_id, e)
        result = f"任务执行失败：{e}"

    next_run = _compute_next_run(task, now)
    await db.update_scheduled_task(
        task_id,
        user_id,
        last_run_at=now.isoformat(timespec="seconds"),
        next_run_at=next_run,
        last_result=result[:4000],
    )
    return {"success": True, "result": result, "next_run_at": next_run}


async def _scheduler_loop():
    """后台调度循环：周期检查到期任务并执行。"""
    while True:
        try:
            due = await db.get_due_scheduled_tasks()
            for task in due:
                try:
                    await run_task_now(int(task["id"]), task.get("user_id", "default"))
                except Exception:
                    logger.warning(
                        "定时任务 %s 调度执行失败", task.get("id"), exc_info=True
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("调度循环异常", exc_info=True)
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


def start_scheduler():
    """创建调度循环后台任务，返回 asyncio.Task。"""
    from .task_manager import task_manager

    return task_manager.create_task(_scheduler_loop(), name="scheduled_task_scheduler")
