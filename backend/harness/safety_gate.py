"""安全门控 — Secure by Default + 纵深防御

策略门控：在规划与执行之间完成：
  - 权限校验（最小权限原则）
  - 敏感数据过滤
  - 注入攻击防御
  - 全链路审计日志
"""

import json as json_mod
import re
import logging
from enum import Enum
from typing import Dict, Any, Optional, List, Callable, Tuple
from dataclasses import dataclass, field
from datetime import datetime

from .sandbox import SandboxManager, IsolationLevel
from .metrics import MetricsCollector, MetricType

logger = logging.getLogger(__name__)


class PermissionLevel(Enum):
    NONE = 0        # 无权限
    READ_ONLY = 1   # 只读
    READ_WRITE = 2  # 读写（标准交易）
    ADMIN = 3       # 管理员


@dataclass
class PermissionCheck:
    """权限校验结果"""
    allowed: bool
    reason: str = ""
    operation: str = ""
    level_required: PermissionLevel = PermissionLevel.NONE
    level_granted: PermissionLevel = PermissionLevel.NONE
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


class AuditLogger:
    """审计日志 — 全链路操作留痕"""
    _logs: List[Dict[str, Any]] = []
    _max_logs: int = 10000
    _on_log: Optional[Callable] = None   # 可选的外部持久化回调

    @classmethod
    def set_persistence_handler(cls, handler: Callable):
        """设置外部持久化回调"""
        cls._on_log = handler

    @classmethod
    def log(cls, event_type: str, details: Dict[str, Any], user_id: str = "system"):
        """记录审计事件"""
        entry = {
            "event_type": event_type,
            "timestamp": datetime.now().isoformat(),
            "user_id": user_id,
            "details": details,
        }
        cls._logs.append(entry)
        if len(cls._logs) > cls._max_logs:
            cls._logs = cls._logs[-cls._max_logs:]
        
        logger.info(f"[AUDIT][{event_type}] user={user_id}: {json_mod.dumps(details, ensure_ascii=False)[:200]}")
        
        if cls._on_log:
            try:
                cls._on_log(entry)
            except Exception:
                logger.warning("审计日志外部持久化回调失败", exc_info=True)

    @classmethod
    def get_recent(cls, limit: int = 100) -> List[Dict]:
        return cls._logs[-limit:]


class SafetyGate:
    """安全门控 — 在 Agent 计划执行前进行安全校验"""
    
    _sandbox: SandboxManager = None
    _metrics: MetricsCollector = None
    _permissions: Dict[str, PermissionLevel] = {}  # user_id → level

    # SQL/命令注入检测模式
    _INJECTION_PATTERNS = [
        r"(?i)(select|insert|update|delete|drop|alter|create|exec|execute)\s",
        r"(?i)(--|;|/\*|\*/)",
        r"(?i)(<script|javascript:|onerror=|onload=)",
        r"(?i)(curl|wget|bash|sh|powershell)\s",
        r"(?i)(rm\s+-rf|del\s+/[fsq])",
    ]

    # 写操作白名单：命中即要求 READ_WRITE（最小权限默认下需显式提权）
    WRITE_OPERATIONS = {"trade", "place_order", "cancel_order", "watchlist_add", "watchlist_remove"}

    def __init__(self, sandbox: Optional[SandboxManager] = None):
        self._sandbox = sandbox or SandboxManager()
        self._metrics = MetricsCollector()
        # 最小权限默认：只读，写操作需显式提权
        self._permissions["default"] = PermissionLevel.READ_ONLY

    def set_user_permission(self, user_id: str, level: PermissionLevel):
        """设置用户权限等级"""
        self._permissions[user_id] = level

    def get_user_permission(self, user_id: str) -> PermissionLevel:
        """获取用户权限等级"""
        return self._permissions.get(user_id, self._permissions.get("default", PermissionLevel.READ_ONLY))

    def check_operation(
        self, operation: str, user_id: str = "default",
        required_level: Optional[PermissionLevel] = None,
        authorized: bool = False,
    ) -> PermissionCheck:
        """校验操作权限。

        最小权限默认：写操作（WRITE_OPERATIONS）要求 READ_WRITE，只读操作要求 READ_ONLY。
        authorized=True 表示用户已显式确认（如确认面板），写操作据此提权到 READ_WRITE。
        """
        user_level = self.get_user_permission(user_id)
        
        # 沙箱隔离检查
        if not self._sandbox.can_execute(operation):
            check = PermissionCheck(
                allowed=False,
                reason=f"Operation '{operation}' requires higher isolation level",
                operation=operation,
                level_required=PermissionLevel.ADMIN,
                level_granted=user_level,
            )
            self._metrics.record(MetricType.PERMISSION_DENIED)
            AuditLogger.log("permission_denied", {
                "operation": operation, "reason": "isolation_level_insufficient",
            }, user_id)
            return check
        
        # 最小权限默认：写操作要求 READ_WRITE，否则 READ_ONLY
        req = required_level or (
            PermissionLevel.READ_WRITE if operation in self.WRITE_OPERATIONS else PermissionLevel.READ_ONLY
        )
        # 显式授权（用户确认面板）视作写操作提权
        if authorized and operation in self.WRITE_OPERATIONS:
            user_level = PermissionLevel.READ_WRITE
        if user_level.value < req.value:
            check = PermissionCheck(
                allowed=False,
                reason=f"Insufficient permissions for '{operation}'",
                operation=operation,
                level_required=req,
                level_granted=user_level,
            )
            self._metrics.record(MetricType.PERMISSION_DENIED)
            AuditLogger.log("permission_denied", {
                "operation": operation, "reason": "insufficient_permission",
                "required": req.name, "granted": user_level.name,
            }, user_id)
            return check
        
        check = PermissionCheck(
            allowed=True,
            operation=operation,
            level_required=req,
            level_granted=user_level,
        )
        AuditLogger.log("permission_granted", {
            "operation": operation, "level": user_level.name,
        }, user_id)
        return check

    def sanitize_input(self, text: str) -> Tuple[str, List[str]]:
        """输入净化：检测并移除潜在的注入攻击"""
        issues = []
        sanitized = text
        
        for pattern in self._INJECTION_PATTERNS:
            if re.search(pattern, text):
                issues.append(f"Potential injection detected: pattern={pattern[:50]}")
                sanitized = re.sub(pattern, '[FILTERED]', sanitized)
        
        if issues:
            logger.warning(f"Input sanitized: {len(issues)} issues detected")
            AuditLogger.log("injection_detected", {"issues": issues})
            self._metrics.record(MetricType.SAFETY_EVENT, len(issues))
        
        return sanitized, issues

    def filter_sensitive_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """过滤敏感数据字段"""
        SENSITIVE_KEYS = {"password", "token", "secret", "api_key", "auth", "credential"}
        filtered = {}
        for k, v in data.items():
            if any(s in k.lower() for s in SENSITIVE_KEYS):
                filtered[k] = "[REDACTED]"
            elif isinstance(v, dict):
                filtered[k] = self.filter_sensitive_data(v)
            else:
                filtered[k] = v
        return filtered
