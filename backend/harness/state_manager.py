"""状态管理器 — 状态分离原则

核心理念：LLM = 无状态计算单元
  - 所有跨轮次状态存储于 Harness 管控的外部持久化引擎
  - 严禁依赖模型自行维护复杂状态
  - 提供状态检查点/快照/回滚能力
"""

import json as json_mod
import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class StateCheckpoint:
    """状态检查点 — 用于回滚和审计"""
    checkpoint_id: str
    agent_name: str
    phase: str           # "before" | "after"
    state_snapshot: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()
        if not self.checkpoint_id:
            self.checkpoint_id = f"ck_{self.agent_name}_{self.phase}_{datetime.now().strftime('%H%M%S%f')}"


class StateManager:
    """状态管理器 — 将 LLM 与状态完全解耦
    
    LLM 视角：每次调用都是全新的，仅需关注上下文注入
    Harness 视角：全权管理状态生命周期（创建/更新/检查点/回滚）
    """

    _instance = None
    _checkpoints: Dict[str, List[StateCheckpoint]] = {}
    _sessions: Dict[str, Dict[str, Any]] = {}
    _max_checkpoints: int = 50

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def init_session(self, session_id: str, initial_state: Dict[str, Any]) -> Dict[str, Any]:
        """初始化会话状态"""
        self._sessions[session_id] = dict(initial_state)
        return self._sessions[session_id]

    def get(self, session_id: str, key: str, default: Any = None) -> Any:
        """读取状态字段"""
        session = self._sessions.get(session_id, {})
        return session.get(key, default)

    def set(self, session_id: str, key: str, value: Any):
        """设置状态字段"""
        if session_id not in self._sessions:
            self._sessions[session_id] = {}
        self._sessions[session_id][key] = value

    def update(self, session_id: str, updates: Dict[str, Any]):
        """批量更新状态"""
        if session_id not in self._sessions:
            self._sessions[session_id] = {}
        self._sessions[session_id].update(updates)

    def snapshot(self, session_id: str) -> Optional[Dict[str, Any]]:
        """获取当前状态快照"""
        return dict(self._sessions.get(session_id, {}))

    def checkpoint(
        self, session_id: str, agent_name: str, phase: str,
        metadata: Optional[Dict] = None,
    ) -> Optional[StateCheckpoint]:
        """创建状态检查点"""
        state = self.snapshot(session_id)
        if state is None:
            return None
        
        ck = StateCheckpoint(
            checkpoint_id="",
            agent_name=agent_name,
            phase=phase,
            state_snapshot=state,
            metadata=metadata or {},
        )
        
        if session_id not in self._checkpoints:
            self._checkpoints[session_id] = []
        self._checkpoints[session_id].append(ck)
        
        # Cleanup old checkpoints
        if len(self._checkpoints[session_id]) > self._max_checkpoints:
            self._checkpoints[session_id] = self._checkpoints[session_id][-self._max_checkpoints:]
        
        logger.debug(f"Checkpoint [{ck.checkpoint_id}]: {agent_name}/{phase}")
        return ck

    def rollback(self, session_id: str, checkpoint_id: str) -> bool:
        """回滚到指定检查点"""
        cks = self._checkpoints.get(session_id, [])
        for ck in cks:
            if ck.checkpoint_id == checkpoint_id:
                self._sessions[session_id] = dict(ck.state_snapshot)
                logger.info(f"Rollback to checkpoint: {checkpoint_id}")
                return True
        return False

    def clear_session(self, session_id: str):
        """清理会话状态"""
        self._sessions.pop(session_id, None)
        self._checkpoints.pop(session_id, None)

    def extract_for_llm(self, session_id: str, keys: List[str]) -> Dict[str, Any]:
        """提取指定字段供 LLM 上下文注入（状态投影）"""
        session = self._sessions.get(session_id, {})
        return {k: session[k] for k in keys if k in session}
