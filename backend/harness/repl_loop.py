"""REPL 闭环容器 — Harness 核心

Harness 的本质，是包裹在 LLM 之外、带边界控制、工具路由与确定性反馈的
REPL（Read-Eval-Print Loop）容器，完整管控 Agent「感知 - 规划 - 行动 - 反思」
的全生命周期。

四个阶段：
  Read（感知管控）
    通过上下文管理器，将外部状态、内部记忆结构化注入，标准化 Agent 的信息输入
  Eval（执行管控）
    通过调用拦截器，捕获工具调用意图，完成合规校验、路由分发、异常监控
  Print（反馈管控）
    通过反馈汇编器，将执行结果结构化封装，重新注入上下文
  Loop（循环管控）
    持续循环直至任务完成或触发终止条件
"""

import asyncio
import logging
import time
from enum import Enum, auto
from typing import Dict, Any, Optional, Callable, Awaitable, List, AsyncGenerator
from dataclasses import dataclass, field
from datetime import datetime

from .context_manager import ContextManager, ContextPriority
from .call_interceptor import CallInterceptor, FallbackChain
from .feedback_assembler import FeedbackAssembler, FeedbackPackage, FeedbackLevel
from .safety_gate import SafetyGate, AuditLogger
from .metrics import MetricsCollector, MetricType, HarnessMetrics
from .resilience import ResilienceManager
from .contracts import ContractRegistry

logger = logging.getLogger(__name__)


class ExecutionPhase(Enum):
    """执行阶段"""
    INIT = auto()           # 初始化
    READ = auto()           # 感知：上下文注入
    PLAN = auto()           # 规划：意图识别/任务拆解
    EXEC = auto()           # 执行：Agent 节点运行
    REFLECT = auto()        # 反思：结果校验/降级
    FEEDBACK = auto()       # 反馈：结构化返回
    TERMINATED = auto()     # 终止


@dataclass
class CycleResult:
    """一次 REPL 循环的结果"""
    phase: ExecutionPhase
    success: bool
    data: Dict[str, Any] = field(default_factory=dict)
    feedback: Optional[FeedbackPackage] = None
    errors: List[str] = field(default_factory=list)
    latency_ms: float = 0.0
    token_estimate: int = 0

    @property
    def ok(self) -> bool:
        return self.success


class HarnessREPL:
    """带边界控制的 REPL 闭环容器

    使用方式：
        harness = HarnessREPL()
        async for cycle in harness.run(graph, initial_state):
            # 处理每个周期的结果
            pass
    """

    _context_mgr: ContextManager
    _interceptor: CallInterceptor
    _feedback: FeedbackAssembler
    _safety: SafetyGate
    _metrics: MetricsCollector
    _resilience: ResilienceManager
    _contracts: ContractRegistry

    def __init__(
        self,
        context_mgr: Optional[ContextManager] = None,
        interceptor: Optional[CallInterceptor] = None,
        safety_gate: Optional[SafetyGate] = None,
        resilience_mgr: Optional[ResilienceManager] = None,
        metrics: Optional[MetricsCollector] = None,
    ):
        self._context_mgr = context_mgr or ContextManager()
        self._interceptor = interceptor or CallInterceptor()
        self._safety = safety_gate or SafetyGate()
        self._feedback = FeedbackAssembler()
        self._resilience = resilience_mgr or ResilienceManager()
        self._contracts = ContractRegistry()

    # ── Read Phase (感知管控) ──

    def read(
        self, state: Dict[str, Any], system_prompt: str = "",
        history_summary: Optional[str] = None, recent_messages: Optional[List[Dict]] = None,
    ) -> str:
        """Read 阶段：结构化注入上下文，标准化 Agent 信息输入"""
        phase_start = time.time()
        self._context_mgr.reset()

        # 1. System Critical: 系统指令（最高优先级）
        if system_prompt:
            self._context_mgr.inject_system_critical(system_prompt)

        # 2. User Intent: 用户当前输入
        user_input = state.get("user_input", "")
        current_time = state.get("current_time", "")
        self._context_mgr.inject_user_intent(user_input, current_time)

        # 3. History: 对话历史（摘要 + 最近消息）
        self._context_mgr.inject_history(
            history_summary or state.get("history_summary"),
            recent_messages or state.get("messages", [{}]),
        )

        # 4. Task Context: 当前任务信息
        strategy = state.get("strategy_direction", "")
        if strategy:
            self._context_mgr.inject_task_context(f"策略方向: {strategy}")

        # 5. Agent Outputs: 聚合上游 Agent 结果
        for key, label in [
            ("quant_assessment", "量化评估"),
            ("intelligence_assessment", "市场情绪评估"),
            ("portfolio_assessment", "持仓评估"),
            ("executor_assessment", "交易评估"),
            ("trade_plan", "交易计划"),
            ("order_result", "交易结果"),
        ]:
            value = state.get(key)
            if value and isinstance(value, dict):
                self._context_mgr.inject_agent_output(key, value, label)

        # 6. Market Data (最低优先)
        market_data = state.get("market_data", {})
        if market_data:
            self._context_mgr.inject_market_data(market_data)

        # 组装
        context = self._context_mgr.build_context()
        self._metrics.record(MetricType.CONTEXT_SIZE, len(context) // 2)

        logger.info(
            f"[REPL:READ] Context assembled: ~{len(context)} chars, ~{len(context)//2} tokens, "
            f"latency {(time.time()-phase_start)*1000:.0f}ms"
        )
        return context

    # ── Eval Phase (执行管控) ──

    async def eval(
        self,
        llm_fn: Callable[..., Awaitable[str]],
        agent_name: str,
        *llm_args,
        context: str = "",
        permissions_check: Optional[str] = None,
        user_id: str = "default",
        **llm_kwargs,
    ) -> CycleResult:
        """Eval 阶段：捕获工具调用意图，合规校验 + 路由分发 + 异常监控"""
        phase_start = time.time()
        
        # 安全门控
        if permissions_check:
            perm = self._safety.check_operation(permissions_check, user_id)
            if not perm.allowed:
                AuditLogger.log("eval_blocked", {
                    "agent": agent_name, "operation": permissions_check, "reason": perm.reason
                }, user_id)
                return CycleResult(
                    phase=ExecutionPhase.EXEC,
                    success=False,
                    errors=[f"Permission denied: {perm.reason}"],
                    feedback=self._feedback.error(
                        agent_name, ExecutionPhase.EXEC.name,
                        errors=[f"Permission denied: {perm.reason}"],
                        suggestions=["Check user permissions"],
                    ),
                    latency_ms=(time.time() - phase_start) * 1000,
                )

        # 合约校验 + JSON 反序列化
        contract = self._contracts.get(agent_name)
        intercept = await self._interceptor.intercept_json_call(
            llm_fn, agent_name, *llm_args, contract=contract, **llm_kwargs
        )

        feedback = None
        if intercept.success:
            feedback = self._feedback.success(
                agent_name, ExecutionPhase.EXEC.name,
                "OK", data=intercept.data,
                metrics={"retries": intercept.retries, "latency_ms": intercept.latency_ms},
            )
        elif intercept.fallback_used:
            feedback = self._feedback.warning(
                agent_name, ExecutionPhase.EXEC.name,
                "Fallback used", intercept.errors, data=intercept.data,
            )
        else:
            feedback = self._feedback.error(
                agent_name, ExecutionPhase.EXEC.name,
                intercept.errors,
                suggestions=["Retry with simplified prompt"],
                fallback_data=intercept.data,
            )

        self._metrics.record(MetricType.FIRST_RESPONSE_LATENCY)
        
        return CycleResult(
            phase=ExecutionPhase.EXEC,
            success=intercept.success or intercept.fallback_used,
            data=intercept.data,
            feedback=feedback,
            errors=intercept.errors,
            latency_ms=intercept.latency_ms,
            token_estimate=len(intercept.raw_output) // 2,
        )

    # ── Print Phase (反馈管控) ──

    def print_feedback(self, cycle: CycleResult, agent_name: str) -> str:
        """Print 阶段：将执行结果结构化封装，准备重新注入"""
        if cycle.feedback:
            return cycle.feedback.to_injectable()
        return FeedbackPackage(
            agent_name=agent_name,
            phase=ExecutionPhase.FEEDBACK.name,
            level=FeedbackLevel.SUCCESS,
            summary="OK" if cycle.success else "Failed",
        ).to_injectable()

    # ── Full REPL Run ──

    async def run(
        self,
        graph_or_fn,
        initial_state: Dict[str, Any],
        user_id: str = "default",
        max_cycles: int = 20,
        session_id: str = "",
    ) -> AsyncGenerator[CycleResult, None]:
        """执行完整的 REPL 循环

        Args:
            graph_or_fn: LangGraph StateGraph 或异步节点函数
            initial_state: AgentState
            user_id: 用户ID
            max_cycles: 最大循环次数（安全阀）
            session_id: 会话ID（用于度量）

        Yields:
            CycleResult: 每个周期的执行结果
        """
        self._metrics.start_session(session_id)
        cycle_count = 0
        state = dict(initial_state)

        # ── Phase 1: READ ──
        context = self.read(
            state,
            system_prompt="",
            history_summary=state.get("history_summary"),
            recent_messages=state.get("messages", [{}]),
        )
        state["_harness_context"] = context

        yield CycleResult(
            phase=ExecutionPhase.READ,
            success=True,
            data={"context_size": len(context)},
            token_estimate=len(context) // 2,
        )

        # ── Phase 2-5: Main Loop ──
        try:
            # 感知：输入净化
            raw_input = state.get("user_input", "")
            sanitized, issues = self._safety.sanitize_input(raw_input)
            if issues:
                state["user_input"] = sanitized
                AuditLogger.log("input_sanitized", {"issues": issues}, user_id)
                yield CycleResult(
                    phase=ExecutionPhase.PLAN,
                    success=True,
                    data={"sanitized": sanitized},
                    errors=[f"Input sanitized: {len(issues)} issues"],
                )

            # 执行 LangGraph / 异步函数
            if hasattr(graph_or_fn, 'ainvoke'):
                # LangGraph StateGraph
                result = await self._resilience.execute(
                    graph_or_fn.ainvoke, state, breaker_name="langgraph",
                )
            elif hasattr(graph_or_fn, 'astream'):
                # Streaming graph
                result = dict(state)
                async for chunk in graph_or_fn.astream(state):
                    for node_name, node_update in chunk.items():
                        for key, value in node_update.items():
                            if key in ("agent_logs", "messages") and isinstance(value, list):
                                result.setdefault(key, []).extend(value)
                            else:
                                result[key] = value
            else:
                # Plain async function
                result = await self._resilience.execute(
                    graph_or_fn, breaker_name="agent_fn",
                )

            cycle_count += 1

            # ── Phase 5: REFLECT ──
            # 校验结果完整性
            reflect_errors = []
            if result.get("intent") is None:
                reflect_errors.append("Missing intent in result")
            if not result.get("final_response") and not result.get("chief_response"):
                reflect_errors.append("No response generated")

            yield CycleResult(
                phase=ExecutionPhase.REFLECT,
                success=len(reflect_errors) == 0,
                data=result,
                errors=reflect_errors,
            )

            # ── Phase 6: FEEDBACK ──
            feedback_pkg = self._feedback.success(
                "harness", ExecutionPhase.FEEDBACK.name,
                f"REPL cycle complete ({cycle_count} cycles)",
                data={
                    "intent": result.get("intent"),
                    "needed_agents": result.get("needed_agents", []),
                },
            )
            yield CycleResult(
                phase=ExecutionPhase.FEEDBACK,
                success=True,
                data=result,
                feedback=feedback_pkg,
            )

        except Exception as e:
            logger.error(f"REPL loop error: {str(e)[:300]}", exc_info=True)
            AuditLogger.log("repl_error", {"error": str(e)}, user_id)
            self._metrics.record(MetricType.LLM_CALL_ERROR, 1)
            
            yield CycleResult(
                phase=ExecutionPhase.TERMINATED,
                success=False,
                errors=[str(e)],
                feedback=self._feedback.fatal(
                    "harness", ExecutionPhase.EXEC.name,
                    errors=[f"REPL loop terminated: {str(e)}"],
                ),
            )

        finally:
            self._metrics.end_session()

    # ── Convenience: Full pipeline for SSE streaming ──

    async def run_graph_stream(
        self,
        graph,                # LangGraph compiled graph
        state: Dict[str, Any],
        user_id: str = "default",
        session_id: str = "",
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """带 Harness 管控的 LangGraph 流式执行
        
        在 graph.astream 基础上增加：
          - 输入净化
          - 安全门控
          - 度量跟踪
          - 审计日志
        """
        self._metrics.start_session(session_id)
        
        try:
            # 输入净化
            raw_input = state.get("user_input", "")
            sanitized, issues = self._safety.sanitize_input(raw_input)
            if issues:
                state["user_input"] = sanitized
                AuditLogger.log("input_sanitized", {"issues": issues}, user_id)

            # 流式执行
            accumulated = dict(state)
            async for chunk in graph.astream(state):
                for node_name, node_update in chunk.items():
                    for key, value in node_update.items():
                        if key in ("agent_logs", "messages") and isinstance(value, list):
                            accumulated.setdefault(key, []).extend(value)
                        else:
                            accumulated[key] = value
                    yield {"node": node_name, "update": node_update}

            # 度量记录
            self._metrics.record(MetricType.TASK_SUCCESS, 1)
            AuditLogger.log("graph_complete", {
                "intent": accumulated.get("intent"),
                "agents": accumulated.get("needed_agents", []),
            }, user_id)

        except Exception as e:
            self._metrics.record(MetricType.LLM_CALL_ERROR, 1)
            AuditLogger.log("graph_error", {"error": str(e)}, user_id)
            raise
        finally:
            self._metrics.end_session()
