"""DeepSeek LLM 双模型客户端 — Flash（轻量快速）+ Pro（深度推理）

Harness 集成：
  - MetricsCollector: 记录 Token 消耗和调用次数
  - ResilienceManager: 熔断器保护
  - CallInterceptor: JSON 确定性反序列化
"""
import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from ..config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_FLASH_MODEL,
    DEEPSEEK_PRO_MODEL,
    HTTP_TIMEOUT_LLM_CHAT,
    HTTP_TIMEOUT_LLM_STREAM,
)

logger = logging.getLogger(__name__)

# 复用全局 httpx.AsyncClient，避免每个请求重复 TCP + TLS 握手（显著降低多 Agent 串行/并行的首字延迟）
_shared_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            timeout=httpx.Timeout(HTTP_TIMEOUT_LLM_STREAM, connect=10.0),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
    return _shared_client


# Harness integration (lazy import to avoid circular deps)
_metrics = None
_resilience = None


def _get_metrics():
    global _metrics
    if _metrics is None:
        from ..harness.metrics import MetricsCollector
        _metrics = MetricsCollector()
    return _metrics


def _get_resilience():
    global _resilience
    if _resilience is None:
        from ..harness.resilience import ResilienceManager
        _resilience = ResilienceManager()
    return _resilience


def _record_token_usage(tokens: int) -> None:
    """记录 LLM 调用与 token 消耗，并按当前 Agent 归集（L5 逐 Agent 观测）"""
    from ..harness.metrics import MetricType, current_agent
    _get_metrics().record(MetricType.LLM_CALL_COUNT, 1)
    _get_metrics().record(MetricType.TOKEN_USAGE, tokens)
    agent = current_agent.get()
    if agent:
        _get_metrics().record_agent_token(agent, tokens)


class DeepSeekClient:
    """DeepSeek API 异步客户端 — 支持指定模型 + Harness 集成"""

    def __init__(self, model: str):
        self.api_key = DEEPSEEK_API_KEY
        self.base_url = DEEPSEEK_BASE_URL.rstrip("/")
        self.model = model

    async def chat(
        self,
        messages: list[dict],
        temperature: float = 0.3,
        max_tokens: int = 2048,
        response_format: dict | None = None,
    ) -> str:
        """调用 DeepSeek Chat Completions API（集成 Harness 度量）"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if response_format:
            body["response_format"] = response_format

        timeout = HTTP_TIMEOUT_LLM_CHAT if self.model == DEEPSEEK_FLASH_MODEL else HTTP_TIMEOUT_LLM_STREAM
        start = time.time()
        
        try:
            r_mgr = _get_resilience()
            from ..harness.metrics import MetricType
            
            async def _call():
                client = _get_http_client()
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers, json=body, timeout=timeout,
                )
                resp.raise_for_status()
                return resp.json()

            data = await r_mgr.execute(
                _call, breaker_name=f"deepseek_{self.model}",
            )
            
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            tokens = usage.get("total_tokens", len(content) // 2)
            
            # Record metrics (LLM calls + tokens, per-agent attribution)
            _record_token_usage(tokens)
            
            latency_ms = (time.time() - start) * 1000
            logger.debug(f"DeepSeek [{self.model}]: {tokens}t, {latency_ms:.0f}ms")
            return content
            
        except httpx.HTTPStatusError as e:
            _get_metrics().record(MetricType.LLM_CALL_ERROR, 1)
            logger.error(f"DeepSeek API 错误 ({self.model}): {e.response.status_code} - {e.response.text[:500]}")
            raise
        except Exception as e:
            _get_metrics().record(MetricType.LLM_CALL_ERROR, 1)
            logger.error(f"DeepSeek API 调用异常 ({self.model}): {e}")
            raise

    async def chat_json(
        self,
        messages: list[dict],
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        """调用 DeepSeek 并强制返回 JSON（含增强降级解析）"""
        raw = await self.chat(messages, temperature, max_tokens)
        raw = raw.strip()
        
        # 使用 Harness FallbackChain 进行多层解析
        from ..harness.call_interceptor import FallbackChain
        
        # Level 1: 标准 Markdown 去围栏
        cleaned = FallbackChain.strip_markdown_fences(raw)
        
        # Level 2: 标准 JSON
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass
        
        # Level 3: 正则提取
        result, method = FallbackChain.try_regex_extract(cleaned)
        if result:
            logger.info(f"DeepSeek JSON 降级解析 [{self.model}]: {method}")
            return result
        
        # Level 4: 最终降级
        logger.warning(f"DeepSeek 返回非 JSON ({self.model}): {raw[:200]}")
        return {"raw": raw, "parse_error": True}

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> tuple:
        """调用 DeepSeek Function Calling，返回 (content, tool_calls)。

        tools 形如 [{"type": "function", "function": {"name", "description", "parameters"}}]
        tool_calls 形如 [{"id", "type", "function": {"name", "arguments"}}]
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
            "tools": tools,
        }
        timeout = HTTP_TIMEOUT_LLM_CHAT if self.model == DEEPSEEK_FLASH_MODEL else HTTP_TIMEOUT_LLM_STREAM
        start = time.time()

        try:
            r_mgr = _get_resilience()
            from ..harness.metrics import MetricType

            async def _call():
                client = _get_http_client()
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers, json=body, timeout=timeout,
                )
                resp.raise_for_status()
                return resp.json()

            data = await r_mgr.execute(_call, breaker_name=f"deepseek_{self.model}")

            msg = data["choices"][0]["message"]
            content = msg.get("content") or ""
            tool_calls = msg.get("tool_calls") or []
            usage = data.get("usage", {})
            tokens = usage.get("total_tokens", 0)

            _record_token_usage(tokens)

            latency_ms = (time.time() - start) * 1000
            logger.debug(
                f"DeepSeek tools [{self.model}]: {tokens}t, {len(tool_calls)} calls, {latency_ms:.0f}ms"
            )
            return content, tool_calls

        except httpx.HTTPStatusError as e:
            _get_metrics().record(MetricType.LLM_CALL_ERROR, 1)
            logger.error(f"DeepSeek tools 错误 ({self.model}): {e.response.status_code} - {e.response.text[:500]}")
            raise
        except Exception as e:
            _get_metrics().record(MetricType.LLM_CALL_ERROR, 1)
            logger.error(f"DeepSeek tools 调用异常 ({self.model}): {e}")
            raise

    async def stream_chat(
        self,
        messages: list[dict],
        temperature: float = 0.5,
        max_tokens: int = 2048,
    ) -> AsyncGenerator[str, None]:
        """Stream tokens from DeepSeek API（集成 Harness 度量）"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        from ..harness.metrics import MetricType
        start = time.time()
        token_count = 0
        
        try:
            client = _get_http_client()
            async with client.stream("POST", f"{self.base_url}/chat/completions",
                    headers=headers, json=body, timeout=HTTP_TIMEOUT_LLM_STREAM) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            delta = data["choices"][0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                token_count += 1
                                yield content
                        except Exception:
                            logger.debug("SSE chunk解析跳过")
                            continue
            
            _record_token_usage(token_count)
            logger.debug(f"DeepSeek stream [{self.model}]: ~{token_count}t, {(time.time()-start)*1000:.0f}ms")
            
        except Exception as e:
            _get_metrics().record(MetricType.LLM_CALL_ERROR, 1)
            logger.error(f"DeepSeek stream 异常 ({self.model}): {e}")
            raise


# 全局实例
# Flash: 用于简单任务（意图分类、普通对话、文本结构化、行情收集）
flash_client = DeepSeekClient(DEEPSEEK_FLASH_MODEL)
# Pro: 用于复杂推理（技术分析研判、交易计划预估、风险综合评估、报告生成）
pro_client = DeepSeekClient(DEEPSEEK_PRO_MODEL)

# 向后兼容的别名
deepseek = flash_client


def get_thinking_mode() -> str:
    """读取当前思考模式（运行时动态读取，支持设置热更新）。"""
    import os
    mode = (os.getenv("THINKING_MODE") or "auto").strip().lower()
    return mode if mode in ("auto", "fast", "deep") else "auto"


def choose_client(deep: bool = True) -> DeepSeekClient:
    """按思考模式选择 LLM 客户端。

    - fast：所有任务走 Flash（关闭深度思考，速度优先）
    - deep：深度分析强制 Pro（思考优先）
    - auto：按场景路由（deep=True 走 Pro，否则走 Flash）
    """
    mode = get_thinking_mode()
    if mode == "fast":
        return flash_client
    if mode == "deep":
        return pro_client
    return pro_client if deep else flash_client
