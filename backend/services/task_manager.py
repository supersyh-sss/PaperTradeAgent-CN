"""异步任务管理器：包装 asyncio.create_task，提供错误处理和生命周期管理"""

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)


class TaskManager:
    def __init__(self):
        self._tasks: set[asyncio.Task] = set()

    def create_task(
        self, coro: Coroutine[Any, Any, Any], name: str = ""
    ) -> asyncio.Task:
        """包装 asyncio.create_task：自动追踪任务并在异常时记录日志"""
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)
        return task

    def _on_task_done(self, task: asyncio.Task):
        self._tasks.discard(task)
        if task.cancelled():
            logger.info(f"后台任务已取消: {task.get_name() or '<unnamed>'}")
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                f"后台任务异常: {task.get_name() or '<unnamed>'} — {exc}",
                exc_info=exc,
            )

    async def startup(self):
        """启动时调用（预留扩展点）"""
        logger.info("TaskManager 已启动")

    async def shutdown(self):
        """关闭时优雅取消所有运行中的任务"""
        if not self._tasks:
            return
        logger.info(f"TaskManager 正在取消 {len(self._tasks)} 个运行中的后台任务...")
        for task in list(self._tasks):
            task.cancel()
        results = await asyncio.gather(*self._tasks, return_exceptions=True)
        for i, result in enumerate(results):
            if isinstance(result, Exception) and not isinstance(
                result, asyncio.CancelledError
            ):
                logger.warning(f"取消任务时出现异常: {result}")
        logger.info("所有后台任务已清理完毕")

    @property
    def running_count(self) -> int:
        return len(self._tasks)


# 全局单例
task_manager = TaskManager()
