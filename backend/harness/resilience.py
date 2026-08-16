"""韧性管理器 — Design for Failure 原则

提供：
  - RetryPolicy: 可配置的重试策略（指数退避 + 随机抖动）
  - CircuitBreaker: 熔断器，防止级联故障
  - 健康恢复机制
"""

import asyncio
import random
import time
import logging
from enum import Enum
from dataclasses import dataclass, field
from typing import Callable, Awaitable, Optional, Any, TypeVar

logger = logging.getLogger(__name__)
T = TypeVar("T")


class CircuitState(Enum):
    CLOSED = "closed"           # 正常通行
    OPEN = "open"               # 熔断中，拒绝请求
    HALF_OPEN = "half_open"     # 试探性恢复


@dataclass
class RetryPolicy:
    """重试策略配置"""
    max_retries: int = 3
    base_delay_ms: float = 500          # 基础延迟（毫秒）
    max_delay_ms: float = 10000         # 最大延迟（毫秒）
    backoff_multiplier: float = 2.0     # 退避乘数
    jitter: bool = True                 # 是否添加随机抖动
    retryable_exceptions: tuple = (Exception,)  # 可重试的异常类型

    def delay_for_attempt(self, attempt: int) -> float:
        """计算第 N 次重试的延迟时间（秒）"""
        delay = min(self.base_delay_ms * (self.backoff_multiplier ** attempt), self.max_delay_ms)
        if self.jitter:
            delay = delay * (0.5 + random.random())
        return delay / 1000.0


@dataclass
class CircuitBreaker:
    """熔断器 — 3种状态：CLOSED / OPEN / HALF_OPEN"""
    name: str
    failure_threshold: int = 5           # 连续失败 N 次后熔断
    recovery_timeout_ms: float = 30000   # 熔断后多久进入 HALF_OPEN
    half_open_max_requests: int = 2      # HALF_OPEN 状态下允许的试探请求数
    
    _state: CircuitState = CircuitState.CLOSED
    _failure_count: int = 0
    _last_failure_time: float = 0.0
    _half_open_count: int = 0

    @property
    def state(self) -> CircuitState:
        """获取当前状态（含自动恢复逻辑）"""
        if self._state == CircuitState.OPEN:
            if time.time() - self._last_failure_time > self.recovery_timeout_ms / 1000.0:
                self._state = CircuitState.HALF_OPEN
                self._half_open_count = 0
                logger.info(f"CircuitBreaker [{self.name}]: OPEN → HALF_OPEN")
        return self._state

    def allow_request(self) -> bool:
        """判断是否允许请求通过"""
        st = self.state
        if st == CircuitState.CLOSED:
            return True
        if st == CircuitState.HALF_OPEN:
            if self._half_open_count < self.half_open_max_requests:
                self._half_open_count += 1
                return True
            return False
        return False  # OPEN

    def record_success(self):
        """记录成功"""
        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            logger.info(f"CircuitBreaker [{self.name}]: HALF_OPEN → CLOSED (recovered)")
        self._failure_count = 0

    def record_failure(self):
        """记录失败"""
        self._failure_count += 1
        self._last_failure_time = time.time()
        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            logger.warning(f"CircuitBreaker [{self.name}]: HALF_OPEN → OPEN (trial failed)")
        elif self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN
            logger.warning(f"CircuitBreaker [{self.name}]: CLOSED → OPEN ({self._failure_count} failures)")


class ResilienceManager:
    """韧性管理器 — 集成重试 + 熔断"""
    _policy: RetryPolicy
    _breakers: dict[str, CircuitBreaker] = {}

    def __init__(self, policy: Optional[RetryPolicy] = None):
        self._policy = policy or RetryPolicy()

    def get_breaker(self, name: str) -> CircuitBreaker:
        """获取或创建指定名称的熔断器"""
        if name not in self._breakers:
            self._breakers[name] = CircuitBreaker(name=name)
        return self._breakers[name]

    async def execute(
        self,
        fn: Callable[..., Awaitable[T]],
        *args,
        breaker_name: Optional[str] = None,
        policy: Optional[RetryPolicy] = None,
        **kwargs,
    ) -> T:
        """执行可调用对象，自动应用重试 + 熔断逻辑"""
        _policy = policy or self._policy
        _breaker = self.get_breaker(breaker_name) if breaker_name else None
        last_error = None

        for attempt in range(_policy.max_retries + 1):
            # 熔断检查
            if _breaker and not _breaker.allow_request():
                raise RuntimeError(f"Circuit breaker [{breaker_name}] is OPEN")

            try:
                result = await fn(*args, **kwargs)
                if _breaker:
                    _breaker.record_success()
                return result
            except _policy.retryable_exceptions as e:
                last_error = e
                if _breaker:
                    _breaker.record_failure()
                
                if attempt < _policy.max_retries:
                    delay = _policy.delay_for_attempt(attempt)
                    logger.warning(
                        f"Retry [{breaker_name or 'default'}] attempt {attempt + 1}/{_policy.max_retries}, "
                        f"delay {delay:.2f}s, error: {str(e)[:100]}"
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        f"All retries exhausted [{breaker_name or 'default'}]: {str(e)[:200]}"
                    )

        raise last_error  # type: ignore
