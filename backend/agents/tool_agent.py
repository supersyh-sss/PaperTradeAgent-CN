"""多轮工具编排循环（L3/A4）— Function Calling 自主取数

把量化/情报 Agent 从「单次 LLM 调用 + 预取数据」升级为「多轮工具调用」，
由 LLM 自主决定查询什么数据、查询几轮。
"""

import json
import logging
from typing import List, Dict, Any, Tuple

from ..services.llm import DeepSeekClient
from ..harness.metrics import MetricsCollector, MetricType
from .tools import AGENT_TOOLS

logger = logging.getLogger(__name__)

# 工具名 → tool 对象，用于 tool_call 分发
TOOL_REGISTRY: Dict[str, Any] = {t.name: t for t in AGENT_TOOLS}


def _tool_parameters(t) -> dict:
    """从 LangChain tool 提取干净的 OpenAI function 参数 schema"""
    sc = getattr(t, "tool_call_schema", None)
    full = None
    if sc is not None and hasattr(sc, "model_json_schema"):
        try:
            full = sc.model_json_schema()
        except Exception:
            full = None
    elif sc is not None and hasattr(sc, "schema"):
        try:
            full = sc.schema()
        except Exception:
            full = None

    if full:
        # 去掉 property 里的 title/default，保留 type/description/enum 等
        props = {}
        for k, v in full.get("properties", {}).items():
            props[k] = {kk: vv for kk, vv in v.items() if kk not in ("title", "default")}
        parameters = {"type": "object", "properties": props}
        required = full.get("required")
        if required:
            parameters["required"] = required
        return parameters
    return {"type": "object", "properties": {}}


def _tools_to_schema(tools: List[Any]) -> List[dict]:
    """将 LangChain @tool 对象转为 OpenAI function-calling schema"""
    schemas = []
    for t in tools:
        schemas.append({
            "type": "function",
            "function": {
                "name": t.name,
                "description": (t.description or "").strip(),
                "parameters": _tool_parameters(t),
            },
        })
    return schemas


def parse_agent_json(content: str) -> dict:
    """解析 Agent 最终输出为 JSON（复用 FallbackChain 多层降级）"""
    from ..harness.call_interceptor import FallbackChain

    text = (content or "").strip()
    cleaned = FallbackChain.strip_markdown_fences(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    result, _method = FallbackChain.try_regex_extract(cleaned)
    if result:
        return result
    return {"raw": text, "parse_error": True}


async def run_tool_agent(
    client: DeepSeekClient,
    system_prompt: str,
    user_prompt: str,
    tools: List[Any],
    max_rounds: int = 3,
    temperature: float = 0.1,
    max_tokens: int = 1024,
) -> Tuple[str, List[dict]]:
    """多轮 Function Calling 循环。

    Returns:
        (final_content, tool_trace) — final_content 为最终文本（通常为 JSON），
        tool_trace 为 [{"tool": name, "args": {...}}, ...] 调用记录。
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    schema = _tools_to_schema(tools)
    trace: List[dict] = []

    for _round in range(max_rounds):
        content, tool_calls = await client.chat_with_tools(
            messages, schema, temperature=temperature, max_tokens=max_tokens
        )

        # 无工具调用 → 视为最终答案
        if not tool_calls:
            return content, trace

        messages.append({
            "role": "assistant",
            "content": content,
            "tool_calls": tool_calls,
        })

        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            arguments = fn.get("arguments") or "{}"
            try:
                args = json.loads(arguments)
            except json.JSONDecodeError:
                args = {}

            tool = TOOL_REGISTRY.get(name)
            success = True
            if tool is None:
                result = {"error": f"unknown tool: {name}"}
                success = False
            else:
                try:
                    result = await tool.ainvoke(args)
                    if isinstance(result, dict) and "error" in result:
                        success = False
                except Exception as e:
                    result = {"error": f"tool {name} failed: {e}"}
                    success = False
                    logger.warning("tool %s failed: %s", name, e)

            # L5 工具调用次数归集：接入 MetricsCollector，服务端指标可见
            metrics = MetricsCollector()
            metrics.record(MetricType.TOOL_CALL_COUNT)
            metrics.record(MetricType.TOOL_CALL_SUCCESS if success else MetricType.TOOL_CALL_FALLBACK)

            trace.append({"tool": name, "args": args})
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", f"call_{_round}_{name}"),
                "content": json.dumps(result, ensure_ascii=False, default=str),
            })

    # 达到最大轮数仍未收敛：返回最后一轮 content，交由上层判定
    return content, trace
