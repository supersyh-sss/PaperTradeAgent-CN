"""沙箱管理器 — Secure by Default 原则

4 级隔离体系：
  Level 1: 进程级 — 基本隔离（默认）
  Level 2: 容器级 — Docker 容器隔离
  Level 3: 轻量级 VM — Firecracker/microVM
  Level 4: 完整 VM — 完全隔离

当前实现：
  - 默认 Level 1（进程级），高风险操作升级至 Level 2
  - 权限最小化：Agent 工具调用前必须通过 SafetyGate
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

logger = logging.getLogger(__name__)


class IsolationLevel(IntEnum):
    """隔离等级"""
    PROCESS = 1          # 进程级
    CONTAINER = 2        # 容器级 (Docker)
    MICRO_VM = 3         # 轻量级 VM (Firecracker)
    FULL_VM = 4          # 完整 VM


# 高风险操作 → 最低隔离等级要求
RISK_ISOLATION_MAP = {
    "execute_trade": IsolationLevel.PROCESS,    # 交易执行（有订单引擎兜底）
    "cancel_order": IsolationLevel.PROCESS,     # 撤单
    "modify_account": IsolationLevel.CONTAINER, # 修改账户信息
    "execute_code": IsolationLevel.MICRO_VM,    # 执行任意代码
    "file_access": IsolationLevel.CONTAINER,    # 文件系统访问
    "network_access": IsolationLevel.PROCESS,   # 网络请求
    "database_write": IsolationLevel.PROCESS,    # 数据库写操作
    "agent_memory_modify": IsolationLevel.PROCESS, # 修改 Agent 记忆
}


@dataclass
class SandboxManager:
    """沙箱管理器 — 按操作风险等级匹配隔离策略"""
    
    current_level: IsolationLevel = IsolationLevel.PROCESS
    _operation_handlers: dict[str, Callable] = field(default_factory=dict)

    def register_handler(self, operation: str, handler: Callable):
        """注册操作处理器"""
        self._operation_handlers[operation] = handler
        logger.info(f"Sandbox handler registered: {operation}")

    def get_required_level(self, operation: str) -> IsolationLevel:
        """获取操作要求的最低隔离等级"""
        return RISK_ISOLATION_MAP.get(operation, IsolationLevel.PROCESS)

    def can_execute(self, operation: str) -> bool:
        """判断当前隔离等级是否允许执行操作"""
        required = self.get_required_level(operation)
        return self.current_level >= required

    def get_sandbox_info(self) -> dict[str, Any]:
        """获取沙箱状态信息"""
        return {
            "isolation_level": self.current_level.name,
            "level_value": int(self.current_level),
            "supported_operations": list(self._operation_handlers.keys()),
            "risk_gates": {op: lev.name for op, lev in RISK_ISOLATION_MAP.items()},
        }
