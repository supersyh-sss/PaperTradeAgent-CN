"""Harness Engineering Framework — 带边界控制的 REPL 闭环容器

Harness 是包裹在 LLM 之外的工程化容器，完整管控 Agent「感知-规划-行动-反思」全生命周期，
将非确定性的模型推理接入确定性的工程体系。

核心机制：
  - REPL Loop: Read(感知管控) → Eval(执行管控) → Print(反馈管控) → Loop(循环管控)
  - Context Pipeline: 信息聚合 → 相关性排序 → 摘要压缩 → 预算分配 → 模板组装
  - Call Interceptor: Schema 序列化 → 触发生成 → 确定性反序列化 → 观测注入
  - State Separation: LLM 作为无状态计算单元，所有跨轮次状态存储于外部持久化引擎
"""

from .repl_loop import HarnessREPL, ExecutionPhase, CycleResult
from .context_manager import ContextManager, ContextBudget, ContextPriority, ContextPipeline
from .call_interceptor import CallInterceptor, InterceptResult, FallbackChain
from .feedback_assembler import FeedbackAssembler, FeedbackPackage
from .safety_gate import SafetyGate, PermissionCheck, AuditLogger
from .metrics import MetricsCollector, MetricType, HarnessMetrics
from .resilience import ResilienceManager, CircuitBreaker, RetryPolicy
from .contracts import ContractRegistry, AgentContract
from .state_manager import StateManager, StateCheckpoint
from .sandbox import SandboxManager, IsolationLevel

__all__ = [
    # REPL Loop
    "HarnessREPL", "ExecutionPhase", "CycleResult",
    # Context Management
    "ContextManager", "ContextBudget", "ContextPriority", "ContextPipeline",
    # Call Interception
    "CallInterceptor", "InterceptResult", "FallbackChain",
    # Feedback Assembly
    "FeedbackAssembler", "FeedbackPackage",
    # Safety & Security
    "SafetyGate", "PermissionCheck", "AuditLogger",
    # Metrics & Observability
    "MetricsCollector", "MetricType", "HarnessMetrics",
    # Resilience
    "ResilienceManager", "CircuitBreaker", "RetryPolicy",
    # Contracts
    "ContractRegistry", "AgentContract",
    # State Management
    "StateManager", "StateCheckpoint",
    # Sandbox
    "SandboxManager", "IsolationLevel",
]
