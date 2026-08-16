"""Agent 并行调度器 — 在单个节点内用 asyncio.gather 实现可靠并行

替代 Send() fan-out：无状态复制问题，agent_logs 不重复。
"""

import asyncio
import copy
import time as _time
from typing import List

from .state import AgentState

# 可并行执行的 Agent 集合
PARALLEL_AGENTS = frozenset({"quant_researcher", "market_intelligence", "portfolio_monitor"})


def _schedule_trace(state: AgentState, agent_name: str, status: str, duration_ms: int, detail: str = "", token_used: int = 0) -> None:
    """异步落库 Agent 执行链路（L5 可观测性，非阻塞，失败静默）"""
    try:
        from ..services.db import record_agent_trace
        asyncio.create_task(record_agent_trace(
            trace_id=state.get("trace_id", ""),
            agent=agent_name,
            status=status,
            duration_ms=duration_ms,
            intent=state.get("intent", ""),
            user_id=state.get("user_id", "default"),
            conversation_id=state.get("conversation_id", ""),
            detail=detail,
            token_used=token_used,
        ))
    except Exception:
        pass


async def dispatch_parallel_agents(state: AgentState, agents: List[str]) -> AgentState:
    """并行执行无依赖关系的 Agent，串行执行有依赖的 Agent"""
    if not agents:
        return state

    tasks = []
    task_names = []

    for agent_name in agents:
        node_func = _AGENT_NODE_MAP.get(agent_name)
        if node_func is None:
            continue
        agent_state = copy.deepcopy(state)
        agent_state["agent_logs"] = []  # 每个 agent 从空列表开始，只记录自身日志
        tasks.append(_traced_node(node_func, agent_state, agent_name, state))
        task_names.append(agent_name)

    if not tasks:
        return state

    results = await asyncio.gather(*tasks, return_exceptions=True)

    for name, result in zip(task_names, results):
        if isinstance(result, Exception):
            continue
        if isinstance(result, dict):
            _merge_result(state, result, name)

    return state


async def _traced_node(node_func, agent_state: AgentState, agent_name: str, state: AgentState):
    """带计时与链路追踪的节点包装（L5：耗时 + token 逐 Agent 归集）"""
    from ..harness.metrics import current_agent, MetricsCollector
    start = _time.perf_counter()
    token = current_agent.set(agent_name)
    try:
        result = await node_func(agent_state)
        duration_ms = int((_time.perf_counter() - start) * 1000)
        tokens = MetricsCollector().take_agent_token(agent_name)
        _schedule_trace(state, agent_name, "ok", duration_ms, token_used=tokens)
        return result
    except Exception as e:
        duration_ms = int((_time.perf_counter() - start) * 1000)
        tokens = MetricsCollector().take_agent_token(agent_name)
        _schedule_trace(state, agent_name, "failed", duration_ms, str(e)[:200], token_used=tokens)
        raise
    finally:
        current_agent.reset(token)


def _merge_result(state: AgentState, result: dict, agent_name: str) -> None:
    """将一个 Agent 的结果合并到主状态（agent_logs 只追加 agent 自身产生的新日志）

    跳过 None 值：Agent 的 deepcopy 包含所有字段（未修改的保留 None），
    直接 set 会覆盖其他 Agent 已写入的结果。"""
    # Known agent output keys — always merge, even if not in initialized state
    for key, value in result.items():
        if key == "messages":
            continue
        if key == "agent_logs":
            state.setdefault("agent_logs", []).extend(value)
            continue
        # Skip None values: prevents later agents from overwriting earlier agents' results
        # (deepcopy carries all keys including None; only merge actual outputs)
        if value is None:
            continue
        # Always set: TypedDict fields initialized as None are still "in" the dict,
        # but for safety, set on all non-protected keys
        if not key.startswith("_"):
            state[key] = value


from .quant_researcher import quant_researcher_node
from .market_intelligence import market_intelligence_node
from .portfolio_monitor import portfolio_monitor_node

# 仅并行调度池内的 Agent。trade_executor 为串行独立节点（依赖并行结果），
# 由 graph 中的独立节点执行，不进入此映射，避免与 PARALLEL_AGENTS 不一致导致重复执行。
_AGENT_NODE_MAP = {
    "quant_researcher": quant_researcher_node,
    "market_intelligence": market_intelligence_node,
    "portfolio_monitor": portfolio_monitor_node,
}
