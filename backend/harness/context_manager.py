"""上下文管理器 — 解决「无限外部状态 vs 有限 Token 窗口」矛盾

构建标准化 Token 转化流水线：
  信息聚合 → 相关性排序 → 摘要压缩 → 预算分配 → 模板组装

核心设计：
  - TokenBudget: 按优先级分配 Token 额度
  - ContextPipeline: 多阶段流水线处理
  - 主动管理模型注意力，将有限 Token 窗口留给核心信息
"""

import logging
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

logger = logging.getLogger(__name__)


class ContextPriority(IntEnum):
    """上下文信息优先级 — 数值越大越优先保留"""

    SYSTEM_CRITICAL = 100  # 系统指令、安全规则
    USER_INTENT = 90  # 用户当前意图
    CURRENT_TASK = 80  # 当前任务上下文
    RECENT_HISTORY = 60  # 近期对话历史
    AGENT_OUTPUT = 50  # Agent 上游分析结果
    MARKET_DATA = 40  # 行情数据
    OLDER_HISTORY = 20  # 较旧的对话
    BACKGROUND = 10  # 背景信息


@dataclass
class ContextBudget:
    """Token 预算分配"""

    total_tokens: int = 4000  # 总预算
    reserved: int = 500  # 为输出预留

    allocations: dict[ContextPriority, int] = field(
        default_factory=lambda: {
            ContextPriority.SYSTEM_CRITICAL: 800,
            ContextPriority.USER_INTENT: 200,
            ContextPriority.CURRENT_TASK: 600,
            ContextPriority.RECENT_HISTORY: 800,
            ContextPriority.AGENT_OUTPUT: 600,
            ContextPriority.MARKET_DATA: 500,
            ContextPriority.OLDER_HISTORY: 300,
            ContextPriority.BACKGROUND: 200,
        }
    )

    @property
    def available(self) -> int:
        return self.total_tokens - self.reserved

    def get_quota(self, priority: ContextPriority) -> int:
        """获取指定优先级的 Token 配额"""
        return self.allocations.get(priority, 200)

    def adjust(self, priority: ContextPriority, delta: int):
        """动态调整某项优先级配额"""
        self.allocations[priority] = max(
            100, self.allocations.get(priority, 200) + delta
        )


@dataclass
class ContextPipeline:
    """上下文转化流水线 — 多阶段处理"""

    budget: ContextBudget = field(default_factory=ContextBudget)
    _stages: list[tuple[int, str]] = field(default_factory=list)  # (priority, content)

    def add(self, priority: ContextPriority, content: str):
        """添加一条上下文信息"""
        self._stages.append((int(priority), content))

    def add_section(self, priority: ContextPriority, header: str, items: list[str]):
        """添加一个结构化段落"""
        if not items:
            return
        combined = header + "\n" + "\n".join(f"  {i}" for i in items)
        self.add(priority, combined)

    def build(self, max_tokens: int | None = None) -> str:
        """按优先级组装上下文，超预算自动截断"""
        # 按优先级降序排列
        sorted_stages = sorted(self._stages, key=lambda x: -x[0])

        budget = max_tokens or self.budget.available
        result_parts = []
        used = 0

        for priority, content in sorted_stages:
            # 估算 Token（中文：约 2 字符 = 1 token）
            est_tokens = len(content) // 2
            quota = min(self.budget.get_quota(ContextPriority(priority)), budget - used)

            if est_tokens <= quota:
                result_parts.append(content)
                used += est_tokens
            else:
                # 截断：保留前半部分（高信息密度）
                chars_to_keep = quota * 2
                truncated = (
                    content[:chars_to_keep]
                    + "\n... [上下文截断，Token预算"
                    + str(quota)
                    + "]"
                )
                result_parts.append(truncated)
                used += quota
                logger.info(
                    f"Context truncated at priority {priority}: {est_tokens} > {quota} tokens"
                )

            if used >= budget:
                logger.info(f"Context budget exhausted: {used}/{budget}")
                break

        self._stages.clear()
        return "\n\n".join(result_parts)

    def clear(self):
        """清空流水线"""
        self._stages.clear()


class ContextManager:
    """上下文管理器 — 全局单例

    负责：
      1. 信息注入（Read 阶段）
      2. Token 预算管理
      3. 上下文压缩（超过阈值触发摘要）
      4. 模板组装
    """

    def __init__(self, budget: ContextBudget | None = None):
        self.budget = budget or ContextBudget()
        self.pipeline = ContextPipeline(budget=self.budget)

    def inject_system_critical(self, system_prompt: str):
        """注入系统级关键指令（最高优先级）"""
        self.pipeline.add(ContextPriority.SYSTEM_CRITICAL, system_prompt)

    def inject_user_intent(self, user_input: str, current_time: str = ""):
        """注入用户意图（次高优先级）"""
        parts = [f"用户消息: {user_input}"]
        if current_time:
            parts.append(f"当前时间: {current_time}")
        self.pipeline.add(ContextPriority.USER_INTENT, "\n".join(parts))

    def inject_history(
        self, history_summary: str | None, recent_messages: list[dict] | None = None
    ):
        """注入对话历史 — 摘要 + 最近消息"""
        if history_summary:
            self.pipeline.add(
                ContextPriority.RECENT_HISTORY, f"对话历史摘要: {history_summary}"
            )

        if recent_messages:
            lines = []
            for m in recent_messages[-8:]:
                role = (
                    "用户"
                    if m.get("role") == "user"
                    else "助手"
                    if m.get("role") == "assistant"
                    else "Agent"
                )
                content = (m.get("content") or "")[:200]
                lines.append(f"[{role}]: {content}")
            if lines:
                self.pipeline.add(
                    ContextPriority.RECENT_HISTORY, "近期对话:\n" + "\n".join(lines)
                )

    def inject_agent_output(
        self, agent_name: str, output: dict[str, Any], label: str = ""
    ):
        """注入上游 Agent 分析结果"""
        import json as json_mod

        content = f"{label or agent_name}分析结果:\n{json_mod.dumps(output, ensure_ascii=False, indent=2)}"
        self.pipeline.add(ContextPriority.AGENT_OUTPUT, content)

    def inject_market_data(self, data: dict[str, Any]):
        """注入行情数据"""
        import json as json_mod

        content = f"行情数据:\n{json_mod.dumps(data, ensure_ascii=False, indent=2)}"
        self.pipeline.add(ContextPriority.MARKET_DATA, content)

    def inject_task_context(self, task_info: str):
        """注入当前任务上下文"""
        self.pipeline.add(ContextPriority.CURRENT_TASK, task_info)

    def build_context(self, max_tokens: int | None = None) -> str:
        """组装最终上下文字符串"""
        return self.pipeline.build(max_tokens)

    def reset(self):
        """重置管理器状态"""
        self.pipeline.clear()
