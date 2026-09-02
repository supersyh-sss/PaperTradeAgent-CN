"""度量收集器 — Everything is Measurable 原则

建立四大类核心指标，反向驱动系统迭代：
  (1) 任务效能：任务成功率、指令遵循度、工具使用有效性
  (2) 服务质量：端到端延迟、首次响应延迟、错误率
  (3) 资源效率：平均 Token 消耗、平均工具调用次数
  (4) 安全合规：策略拒绝率、安全事件数
"""

import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, ClassVar

logger = logging.getLogger(__name__)

# 当前执行 Agent 的上下文标记（asyncio 下每个 task 独立隔离），
# 用于在 LLM 调用层把 token 消耗归集到具体 Agent，打通 L5 逐 Agent 观测。
current_agent: ContextVar[str] = ContextVar("current_agent", default="")


class MetricType(Enum):
    """度量类型枚举"""

    # 任务效能
    TASK_SUCCESS = auto()  # 任务是否成功
    TASK_INTENT_ACCURACY = auto()  # 意图识别准确度
    TOOL_CALL_SUCCESS = auto()  # 工具调用成功
    TOOL_CALL_FALLBACK = auto()  # 工具调用触发降级
    # 服务质量
    END_TO_END_LATENCY = auto()  # 端到端延迟
    FIRST_RESPONSE_LATENCY = auto()  # 首次响应延迟
    ERROR_RATE = auto()  # 错误率
    LLM_CALL_COUNT = auto()  # LLM 调用次数
    LLM_CALL_ERROR = auto()  # LLM 调用失败
    # 资源效率
    TOKEN_USAGE = auto()  # Token 消耗
    TOOL_CALL_COUNT = auto()  # 工具调用次数
    CONTEXT_SIZE = auto()  # 上下文大小
    # 安全合规
    PERMISSION_DENIED = auto()  # 权限拒绝
    SAFETY_EVENT = auto()  # 安全事件
    AUDIT_EVENT = auto()  # 审计事件


@dataclass
class HarnessMetrics:
    """单次会话的度量快照"""

    session_id: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    # Task efficacy
    tasks_total: int = 0
    tasks_success: int = 0
    intent_correct: int = 0
    tool_calls_total: int = 0
    tool_calls_success: int = 0
    tool_calls_fallback: int = 0
    # Quality of service
    llm_calls_total: int = 0
    llm_calls_error: int = 0
    first_response_time: float = 0.0
    # Resource efficiency
    total_tokens: int = 0
    context_sizes: list[int] = field(default_factory=list)
    # Safety
    permission_denied: int = 0
    safety_events: int = 0

    @property
    def success_rate(self) -> float:
        if self.tasks_total == 0:
            return 1.0
        return self.tasks_success / self.tasks_total

    @property
    def latencies(self) -> dict[str, float]:
        return {
            "end_to_end_s": self.end_time - self.start_time if self.end_time else 0,
            "first_response_s": self.first_response_time,
        }

    @property
    def avg_context_size(self) -> float:
        if not self.context_sizes:
            return 0.0
        return sum(self.context_sizes) / len(self.context_sizes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "success_rate": self.success_rate,
            "latencies": self.latencies,
            "llm_calls": {
                "total": self.llm_calls_total,
                "errors": self.llm_calls_error,
            },
            "tool_calls": {
                "total": self.tool_calls_total,
                "success": self.tool_calls_success,
                "fallback": self.tool_calls_fallback,
            },
            "tokens": self.total_tokens,
            "avg_context_size": self.avg_context_size,
            "safety": {
                "permission_denied": self.permission_denied,
                "events": self.safety_events,
            },
        }


class MetricsCollector:
    """全局度量收集器 — 单例"""

    _instance = None
    _current: HarnessMetrics | None = None
    _history: ClassVar[list[dict[str, Any]]] = []
    _max_history: int = 1000
    _agent_tokens: ClassVar[dict[str, int]] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def start_session(self, session_id: str = "") -> HarnessMetrics:
        """开始新会话度量"""
        self._current = HarnessMetrics(
            session_id=session_id
            or f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            start_time=time.time(),
        )
        return self._current

    def record(self, metric_type: MetricType, value: Any = 1, meta: dict | None = None):
        """记录一条度量"""
        if not self._current:
            return
        m = self._current

        match metric_type:
            case MetricType.TASK_SUCCESS:
                m.tasks_success += int(value)
                m.tasks_total += 1
            case MetricType.LLM_CALL_COUNT:
                m.llm_calls_total += int(value)
            case MetricType.LLM_CALL_ERROR:
                m.llm_calls_error += int(value)
            case MetricType.TOOL_CALL_SUCCESS:
                m.tool_calls_success += int(value)
            case MetricType.TOOL_CALL_COUNT:
                m.tool_calls_total += int(value)
            case MetricType.TOOL_CALL_FALLBACK:
                m.tool_calls_fallback += int(value)
            case MetricType.TOKEN_USAGE:
                m.total_tokens += int(value)
            case MetricType.CONTEXT_SIZE:
                m.context_sizes.append(int(value))
            case MetricType.FIRST_RESPONSE_LATENCY:
                if m.first_response_time == 0.0:
                    m.first_response_time = time.time() - m.start_time
            case MetricType.PERMISSION_DENIED:
                m.permission_denied += int(value)
            case MetricType.SAFETY_EVENT:
                m.safety_events += int(value)

    def end_session(self) -> HarnessMetrics | None:
        """结束会话，归档度量"""
        if self._current:
            self._current.end_time = time.time()
            self._history.append(self._current.to_dict())
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history :]
            result = self._current
            self._current = None
            logger.info(f"Session metrics: {result.to_dict()}")
            return result
        return None

    def get_summary(self) -> dict[str, Any]:
        """获取汇总统计"""
        if not self._history:
            return {"sessions": 0}
        success_rates = [h["success_rate"] for h in self._history]
        llm_errors = sum(h["llm_calls"]["errors"] for h in self._history)
        total_tokens = sum(h["tokens"] for h in self._history)
        return {
            "sessions": len(self._history),
            "avg_success_rate": sum(success_rates) / len(success_rates)
            if success_rates
            else 0,
            "total_llm_errors": llm_errors,
            "total_tokens": total_tokens,
            "latest": self._history[-1] if self._history else None,
        }

    def record_agent_token(self, agent: str, tokens: int) -> None:
        """累计某个 Agent 的 token 消耗（由 LLM 调用层归集）"""
        if not agent:
            return
        self._agent_tokens[agent] = self._agent_tokens.get(agent, 0) + int(tokens or 0)

    def take_agent_token(self, agent: str) -> int:
        """读取并清零某个 Agent 本次执行累计的 token"""
        return self._agent_tokens.pop(agent, 0)
