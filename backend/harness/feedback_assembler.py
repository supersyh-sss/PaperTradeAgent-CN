"""反馈汇编器 — Print 阶段管控

将执行结果（成功/异常）结构化封装，重新注入上下文，形成确定性反馈。

设计原则：
  - 成功反馈：结构化数据 + 执行摘要
  - 异常反馈：错误码 + 降级信息 + 可操作建议
  - 统一格式，确保 LLM 可确定性解析
"""

import json as json_mod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class FeedbackLevel(Enum):
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    FATAL = "fatal"


@dataclass
class FeedbackPackage:
    """结构化反馈包"""
    agent_name: str
    phase: str           # "planning" | "execution" | "reflection"
    level: FeedbackLevel = FeedbackLevel.SUCCESS
    summary: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    def to_injectable(self) -> str:
        """格式化为可注入上下文的标准文本"""
        lines = [
            f"[Feedback:{self.agent_name}/{self.phase}] Level={self.level.value}",
            f"Summary: {self.summary}",
        ]
        if self.data:
            lines.append(f"Data: {json_mod.dumps(self.data, ensure_ascii=False)}")
        if self.errors:
            lines.append(f"Errors: {'; '.join(self.errors)}")
        if self.warnings:
            lines.append(f"Warnings: {'; '.join(self.warnings)}")
        if self.suggestions:
            lines.append(f"Suggestions: {'; '.join(self.suggestions)}")
        if self.metrics:
            lines.append(f"Metrics: {json_mod.dumps(self.metrics, ensure_ascii=False)}")
        return "\n".join(lines)


class FeedbackAssembler:
    """反馈汇编器 — 统一封装所有执行反馈"""

    @staticmethod
    def success(
        agent_name: str, phase: str, summary: str = "", data: dict | None = None,
        metrics: dict | None = None,
    ) -> FeedbackPackage:
        """构造成功反馈"""
        return FeedbackPackage(
            agent_name=agent_name, phase=phase,
            level=FeedbackLevel.SUCCESS, summary=summary,
            data=data or {}, metrics=metrics or {},
        )

    @staticmethod
    def warning(
        agent_name: str, phase: str, summary: str, warnings: list[str],
        data: dict | None = None,
    ) -> FeedbackPackage:
        """构造警告反馈"""
        return FeedbackPackage(
            agent_name=agent_name, phase=phase,
            level=FeedbackLevel.WARNING, summary=summary,
            warnings=warnings, data=data or {},
        )

    @staticmethod
    def error(
        agent_name: str, phase: str, errors: list[str],
        suggestions: list[str] | None = None,
        fallback_data: dict | None = None,
    ) -> FeedbackPackage:
        """构造错误反馈（含降级建议）"""
        return FeedbackPackage(
            agent_name=agent_name, phase=phase,
            level=FeedbackLevel.ERROR,
            errors=errors,
            suggestions=suggestions or ["Retry the request with corrected parameters"],
            data=fallback_data or {},
            summary=f"Failed: {'; '.join(errors)}",
        )

    @staticmethod
    def fatal(
        agent_name: str, phase: str, errors: list[str],
    ) -> FeedbackPackage:
        """构造致命错误反馈"""
        return FeedbackPackage(
            agent_name=agent_name, phase=phase,
            level=FeedbackLevel.FATAL,
            errors=errors,
            suggestions=["Stop execution and notify user"],
            summary=f"Fatal: {'; '.join(errors)}",
        )

    @staticmethod
    def aggregate(feedbacks: list[FeedbackPackage]) -> str:
        """聚合多个反馈包为一段注入文本"""
        parts = ["=== Execution Feedback Report ==="]
        for fb in feedbacks:
            parts.append(fb.to_injectable())
            parts.append("---")
        if not feedbacks:
            parts.append("No feedback generated.")
        return "\n".join(parts)
