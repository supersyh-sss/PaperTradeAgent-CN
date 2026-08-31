"""Harness Engineering Framework — 带边界控制的 REPL 闭环容器

Harness 是包裹在 LLM 之外的工程化容器，完整管控 Agent「感知-规划-行动-反思」全生命周期，
将非确定性的模型推理接入确定性的工程体系。

核心机制：
  - REPL Loop: Read(感知管控) → Eval(执行管控) → Print(反馈管控) → Loop(循环管控)
  - Context Pipeline: 信息聚合 → 相关性排序 → 摘要压缩 → 预算分配 → 模板组装
  - Call Interceptor: Schema 序列化 → 触发生成 → 确定性反序列化 → 观测注入
  - State Separation: LLM 作为无状态计算单元，所有跨轮次状态存储于外部持久化引擎
"""

from .call_interceptor import CallInterceptor, FallbackChain, InterceptResult
from .context_manager import (
    ContextBudget,
    ContextManager,
    ContextPipeline,
    ContextPriority,
)
from .contracts import AgentContract, ContractRegistry
from .feedback_assembler import FeedbackAssembler, FeedbackPackage
from .metrics import HarnessMetrics, MetricsCollector, MetricType
from .repl_loop import CycleResult, ExecutionPhase, HarnessREPL
from .resilience import CircuitBreaker, ResilienceManager, RetryPolicy
from .safety_gate import AuditLogger, PermissionCheck, SafetyGate
from .sandbox import IsolationLevel, SandboxManager
from .state_manager import StateCheckpoint, StateManager

__all__ = [
    # Contracts
    "AgentContract",
    # Safety & Security
    "AuditLogger",
    # Call Interception
    "CallInterceptor",
    # Resilience
    "CircuitBreaker",
    # Context Management
    "ContextBudget",
    "ContextManager",
    "ContextPipeline",
    "ContextPriority",
    "ContractRegistry",
    # REPL Loop
    "CycleResult",
    "ExecutionPhase",
    "FallbackChain",
    # Feedback Assembly
    "FeedbackAssembler",
    "FeedbackPackage",
    # Metrics & Observability
    "HarnessMetrics",
    "HarnessREPL",
    "InterceptResult",
    # Sandbox
    "IsolationLevel",
    "MetricType",
    "MetricsCollector",
    "PermissionCheck",
    "ResilienceManager",
    "RetryPolicy",
    "SafetyGate",
    "SandboxManager",
    # State Management
    "StateCheckpoint",
    "StateManager",
]
