"""调用拦截器 — 解决「文本预测输出 vs 物理世界执行」矛盾

Function Calling 全生命周期管控：
  Schema 序列化 → 触发生成 → 确定性反序列化 → 观测注入

降级路径：
  - JSON 反序列化失败 → 文本解析回退
  - 契约校验失败 → 重试 + 修正
  - 执行异常 → 交互式补全 / 反思重规划
"""

import json as json_mod
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .contracts import AgentContract, ContractRegistry
from .metrics import MetricsCollector, MetricType
from .resilience import ResilienceManager, RetryPolicy

logger = logging.getLogger(__name__)


@dataclass
class InterceptResult:
    """拦截结果"""
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    raw_output: str = ""
    errors: list[str] = field(default_factory=list)
    retries: int = 0
    fallback_used: bool = False
    latency_ms: float = 0.0


class FallbackChain:
    """降级链路 — 按优先级尝试多种解析策略"""
    
    @staticmethod
    def strip_markdown_fences(raw: str) -> str:
        """移除 Markdown 代码围栏"""
        text = raw.strip()
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        text = text.removesuffix("```")
        return text.strip()

    @staticmethod
    def try_json_parse(raw: str) -> tuple[dict | None, str]:
        """尝试标准 JSON 解析"""
        try:
            return json_mod.loads(raw), "json_parse"
        except json_mod.JSONDecodeError:
            return None, ""

    @staticmethod
    def try_regex_extract(raw: str) -> tuple[dict | None, str]:
        """文本正则回退：尝试从文本中提取 JSON 块"""
        # 匹配最外层的 {...} 或 [...] 
        match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', raw, re.DOTALL)
        if match:
            try:
                return json_mod.loads(match.group()), "regex_extract"
            except json_mod.JSONDecodeError:
                pass
        
        # 尝试逐行 key: value 解析
        lines = raw.strip().split("\n")
        result = {}
        for line in lines:
            line = line.strip()
            if ":" in line:
                key, _, value = line.partition(":")
                key = key.strip().strip('"').strip("'")
                value = value.strip().strip(",").strip('"').strip("'")
                if key and value:
                    result[key] = value
        if result:
            return result, "line_parse"
        return None, ""

    @staticmethod
    def parse(raw: str) -> tuple[dict[str, Any], str, list[str]]:
        """执行完整降级链路"""
        cleaned = FallbackChain.strip_markdown_fences(raw)
        errors = []
        
        # Level 1: 标准 JSON
        result, method = FallbackChain.try_json_parse(cleaned)
        if result:
            return result, method, errors
        
        errors.append("JSON parse failed")
        
        # Level 2: 正则提取
        result, method = FallbackChain.try_regex_extract(cleaned)
        if result:
            errors.append(f"Fallback to {method} successful")
            return result, method, errors
        
        errors.append("All parsing strategies exhausted")
        return {"raw": cleaned, "parse_error": True}, "exhausted", errors


class CallInterceptor:
    """调用拦截器 — Eval 阶段管控
    
    职责：
      1. 拦截 LLM 调用 → JSON 确定性反序列化
      2. 契约校验 → 字段类型修正
      3. 失败降级 → 重试/回退
      4. 度量收集
    """

    _contracts: ContractRegistry = None
    _resilience: ResilienceManager = None
    _metrics: MetricsCollector = None

    def __init__(
        self,
        resilience: ResilienceManager | None = None,
        contracts: ContractRegistry | None = None,
    ):
        self._resilience = resilience or ResilienceManager()
        self._contracts = contracts or ContractRegistry()
        self._metrics = MetricsCollector()

    async def intercept_json_call(
        self,
        llm_fn: Callable[..., Awaitable[str]],
        agent_name: str,
        *llm_args,
        contract: AgentContract | None = None,
        retry_policy: RetryPolicy | None = None,
        **llm_kwargs,
    ) -> InterceptResult:
        """拦截 LLM 调用，确保返回合法 JSON
        
        Args:
            llm_fn: LLM 调用函数（异步，返回字符串）
            agent_name: Agent 名称（用于契约查找和度量标记）
            contract: Agent 输出契约（可选）
            retry_policy: 重试策略（可选）
            llm_args/llm_kwargs: 传给 llm_fn 的参数
        
        Returns:
            InterceptResult: 包含解析后数据和诊断信息
        """
        start = time.time()
        result = InterceptResult()
        
        # 查找契约
        if not contract:
            contract = self._contracts.get(agent_name)
        
        max_retries = contract.max_retries if contract else 3
        policy = retry_policy or RetryPolicy(max_retries=max_retries)
        
        last_raw = ""
        
        for attempt in range(max_retries + 1):
            try:
                # 1. 调用 LLM
                self._metrics.record(MetricType.LLM_CALL_COUNT, 1)
                last_raw = await llm_fn(*llm_args, **llm_kwargs)
                
                # 2. 反序列化 → 降级链路
                parsed, method, errors = FallbackChain.parse(last_raw)
                
                if method == "exhausted":
                    result.errors = errors
                    if attempt < max_retries:
                        logger.warning(f"[{agent_name}] Parse failed, retry {attempt+1}/{max_retries}")
                        continue
                    else:
                        result.data = parsed
                        result.fallback_used = True
                        self._metrics.record(MetricType.TOOL_CALL_FALLBACK, 1)
                        break
                
                # 3. 契约校验
                if contract:
                    validated, issues = self._contracts.validate(agent_name, parsed)
                    result.errors = issues
                    if issues:
                        logger.warning(f"[{agent_name}] Contract validation: {issues}")
                    
                    # 仅缺少关键字段时重试
                    critical_missing = any("Missing required field" in str(i) for i in issues)
                    if critical_missing and attempt < max_retries:
                        logger.warning(f"[{agent_name}] Critical fields missing, retry {attempt+1}/{max_retries}")
                        continue
                    
                    result.data = validated
                else:
                    result.data = parsed
                
                result.success = True
                result.retries = attempt
                self._metrics.record(MetricType.TOOL_CALL_SUCCESS, 1)
                logger.info(f"[{agent_name}] Intercepted: method={method}, retries={attempt}")
                break
                
            except Exception as e:
                self._metrics.record(MetricType.LLM_CALL_ERROR, 1)
                logger.error(f"[{agent_name}] LLM call failed: {str(e)[:200]}")
                result.errors.append(str(e))
                
                if attempt < max_retries:
                    import asyncio
                    delay = policy.delay_for_attempt(attempt)
                    await asyncio.sleep(delay)
                else:
                    result.data = {"raw": last_raw, "parse_error": True, "error": str(e)}
                    result.fallback_used = True
                    self._metrics.record(MetricType.TOOL_CALL_FALLBACK, 1)
                    break
        
        result.raw_output = last_raw
        result.latency_ms = (time.time() - start) * 1000
        return result

    def sanitize_for_display(self, data: dict[str, Any]) -> dict[str, Any]:
        """脱敏处理，移除内部标记"""
        return {k: v for k, v in data.items() if not k.startswith("_")}
