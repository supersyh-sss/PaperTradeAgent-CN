"""聊天 API - LangGraph Agent 编排入口（Harness Enhanced）

Harness 集成点：
  - 输入净化：SafetyGate.sanitize_input
  - 审计日志：AuditLogger 关键操作
  - 安全门控：权限校验
"""

import asyncio
import json as json_mod
import logging
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

from ..agents.graph import trading_graph
from ..agents.state import create_initial_state

# Harness infrastructure
from ..harness.safety_gate import AuditLogger, SafetyGate
from ..middleware.error_handler import get_current_user
from ..services import db
from ..services.logging_config import get_trace_id
from ..services.symbol import pure_code

# Global harness instances
_safety_gate = SafetyGate()

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _strip_markdown(text: str) -> str:
    """移除常见 Markdown 标记，确保输出为纯文本"""
    # Remove bold/italic
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    # Remove headers
    text = re.sub(r"^#{1,4}\s+", "", text, flags=re.MULTILINE)
    # Remove inline code
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Remove list markers (but keep content)
    text = re.sub(r"^[\-\*\+]\s+", "— ", text, flags=re.MULTILINE)
    # Remove table formatting
    text = re.sub(r"\|([^|]+)\|", r"\1", text)
    # Remove consecutive blank lines (keep at most 1)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# 分析类意图（调度了专项 Agent 并产出结果）应基于 Agent 输出合成回复，
# 而不是用首席的快速 chat_reply，避免"没拿到数据"或丢失分析结果。
_SYNTHESIZE_INTENTS = ("portfolio", "analyze", "market")


def _has_agent_output(state: dict) -> bool:
    """是否已有非首席策略的 Agent 分析输出。"""
    return any(
        l.get("agent") != "chief_strategist" for l in state.get("agent_logs", [])
    )


async def _generate_non_report_reply(final_state: dict) -> str:
    """生成 needs_report=false 时的最终回复。

    分析类意图（portfolio/analyze/market）优先基于 Agent 输出合成；
    否则回退首席快速回复；再回退通用 Flash 生成。
    """
    intent = final_state.get("intent", "chat")
    chief_reply = final_state.get("chief_response", "")

    if intent in _SYNTHESIZE_INTENTS and _has_agent_output(final_state):
        try:
            from ..agents.prompts import RESPONSE_GENERATOR_SYSTEM
            from ..agents.response_generator import _build_context
            from ..services.llm import flash_client

            context = _build_context(final_state)
            llm_messages = [
                {"role": "system", "content": RESPONSE_GENERATOR_SYSTEM},
                {"role": "user", "content": context},
            ]
            return await flash_client.chat(
                llm_messages, temperature=0.5, max_tokens=512
            )
        except Exception:
            logger.warning("分析类意图响应合成失败，回退首席回复", exc_info=True)

    if chief_reply:
        return chief_reply

    try:
        from ..agents.prompts import RESPONSE_GENERATOR_SYSTEM
        from ..agents.response_generator import _build_context
        from ..services.llm import flash_client

        context = _build_context(final_state)
        llm_messages = [
            {"role": "system", "content": RESPONSE_GENERATOR_SYSTEM},
            {"role": "user", "content": context},
        ]
        return await flash_client.chat(llm_messages, temperature=0.5, max_tokens=512)
    except Exception:
        logger.exception("Flash模型响应生成失败，使用降级回复")
        return "（数据暂不可用，以下为降级提示）你好！我是A股模拟交易助手，可以帮你分析股票、模拟交易、查看持仓。请问有什么可以帮你的？"


class ChatRequest(BaseModel):
    message: str
    execute_trade: bool = False  # 是否确认执行交易
    trade_side: str | None = None
    trade_quantity: int | None = None
    current_time: str | None = None  # ISO format: 2026-08-11T14:30:00+08:00
    conversation_id: str | None = None  # 复用已有会话ID（续接对话）


class TradeActionRequest(BaseModel):
    action: str  # "confirm" or "cancel"
    action_type: str | None = (
        None  # trade | watchlist_add | watchlist_remove | cancel_order
    )
    trade_plan: dict | None = None
    data: dict | None = None
    conversation_id: str | None = None
    order_id: str | None = None  # 用于撤销特定订单


class ReportRequest(BaseModel):
    conversation_id: str
    intent: str = "analyze"


# Agent identity lookup (mirrors frontend AGENT_INFO)
AGENT_INFO_MAP = {
    "chief_strategist": {"name_cn": "首席策略官", "color": "#60a5fa"},
    "quant_researcher": {"name_cn": "量化研究员", "color": "#a78bfa"},
    "market_intelligence": {"name_cn": "市场动态感知官", "color": "#34d399"},
    "trade_executor": {"name_cn": "交易执行员", "color": "#f87171"},
    "portfolio_monitor": {"name_cn": "风控持仓监控官", "color": "#fbbf24"},
    "response_generator": {"name_cn": "综合研判官", "color": "#fb923c"},
}


@router.post("")
async def chat(req: ChatRequest, user_id: str = Depends(get_current_user)):
    """处理用户对话，通过 LangGraph Agent 编排"""
    # 创建初始状态
    state = create_initial_state(req.message, user_id, current_time=req.current_time)

    # 如果请求中包含交易确认信息
    if req.execute_trade:
        state["trade_side"] = req.trade_side or "BUY"
        state["trade_quantity"] = req.trade_quantity or 100

    # Load conversation context (trimmed with summary) for existing conversations
    conversation_id = state.get("conversation_id", "")
    try:
        conversation_context = await db.get_conversation_context(conversation_id)
        summary = conversation_context.get("summary")
        recent = conversation_context.get("recent_messages", [])
        if recent and not summary:
            history_lines = []
            for m in recent[-20:]:
                role_label = (
                    "用户"
                    if m.get("role") == "user"
                    else "助手"
                    if m.get("role") == "assistant"
                    else "Agent"
                )
                content = (m.get("content") or "")[:200]
                ts = (m.get("created_at") or "")[:16]
                history_lines.append(f"[{ts}][{role_label}]: {content}")
            state["history_summary"] = (
                "近期对话（带时间戳，最近的在最后）:\n" + "\n".join(history_lines)
            )
        elif summary:
            history_lines = [f"EARLIER对话摘要（非本次会话的早期内容）: {summary}"]
            if recent:
                history_lines.append("最近对话（带时间戳）:")
                for m in recent[-10:]:
                    role_label = (
                        "用户"
                        if m.get("role") == "user"
                        else "助手"
                        if m.get("role") == "assistant"
                        else "Agent"
                    )
                    content = (m.get("content") or "")[:200]
                    ts = (m.get("created_at") or "")[:16]
                    history_lines.append(f"[{ts}][{role_label}]: {content}")
            state["history_summary"] = "\n".join(history_lines)
    except Exception:
        logger.warning("会话上下文加载失败", exc_info=True)

    # 执行 LangGraph 状态图
    try:
        result = await trading_graph.ainvoke(state)
    except Exception as e:
        result = state
        result["final_response"] = f"系统处理出错: {e!s}"
        result["messages"] = [
            {"role": "user", "content": req.message},
            {"role": "assistant", "content": result["final_response"], "metadata": {}},
        ]

    # Generate final response (graph nodes don't produce final_response text)
    needs_report = result.get("needs_report")
    if needs_report is None:
        needs_report = False
    if not needs_report:
        result["final_response"] = await _generate_non_report_reply(result)
    else:
        try:
            from ..agents.prompts import RESPONSE_GENERATOR_SYSTEM
            from ..agents.response_generator import _build_context
            from ..services.llm import choose_client

            context = _build_context(result)
            llm_messages = [
                {"role": "system", "content": RESPONSE_GENERATOR_SYSTEM},
                {"role": "user", "content": context},
            ]
            result["final_response"] = await choose_client(True).chat(
                llm_messages, temperature=0.5, max_tokens=3072
            )
        except Exception:
            logger.exception("Pro模型报告生成失败，使用降级回复")
            try:
                from ..agents.response_generator import _fallback_response

                result["final_response"] = _fallback_response(result)
            except Exception:
                logger.exception("报告生成完全失败")
                result["final_response"] = "报告生成失败，请重试。"

    conversation_id = result.get("conversation_id", "")

    # 持久化对话历史
    # 1. 创建会话（新会话自动插入，已存在则忽略）
    await db.create_conversation(conversation_id, user_id)

    # 2. 检查是否为全新会话（无历史消息），是则用首条用户消息更新标题
    existing_msgs = await db.get_conversation_messages(conversation_id)
    if not existing_msgs:
        title = req.message[:20]
        await db.update_conversation_title(conversation_id, title)

    # 3. 保存用户消息
    await db.save_message(conversation_id, "user", req.message)

    # 4. 保存 agent_logs（各 Agent 的思考过程）
    for log in result.get("agent_logs", []):
        await db.save_message(
            conversation_id,
            "agent_log",
            log.get("content", ""),
            agent_name=log.get("agent", ""),
            agent_emoji=log.get("emoji", ""),
            metadata=json_mod.dumps(log.get("metadata", {})),
        )
        for fu in log.get("followups", []):
            await db.save_message(
                conversation_id,
                "agent_log",
                fu,
                agent_name=log.get("agent", ""),
                agent_emoji=log.get("emoji", ""),
                metadata=json_mod.dumps({"is_followup": True}),
            )

    # 5. 保存 assistant 最终回复
    await db.save_message(
        conversation_id,
        "assistant",
        result.get("final_response", ""),
        metadata=json_mod.dumps(
            {
                "intent": result.get("intent", "chat"),
                "active_symbol": result.get("active_symbol"),
                "needs_report": result.get("needs_report", False),
            }
        ),
    )

    # 返回结构化结果
    return {
        "response": result.get("final_response", ""),
        "intent": result.get("intent", "chat"),
        "active_symbol": result.get("active_symbol"),
        "active_name": result.get("active_name"),
        "in_watchlist": result.get("in_watchlist", False),
        "technical_analysis": result.get("technical_analysis"),
        "market_intelligence": result.get("market_intelligence"),
        "trade_plan": result.get("trade_plan"),
        "order_result": result.get("order_result"),
        "portfolio_summary": result.get("portfolio_summary"),
        "agent_logs": result.get("agent_logs", []),
        "messages": result.get("messages", []),
        "conversation_id": conversation_id,
    }


@router.post("/stream")
async def chat_stream(req: ChatRequest, user_id: str = Depends(get_current_user)):
    """SSE streaming: Agent气泡内容逐字输出。报告不流式，仅返回完整文本。"""
    # Validate message is not empty
    if not req.message or not req.message.strip():
        return StreamingResponse(
            _error_event_generator("消息不能为空"),
            media_type="text/event-stream",
        )

    # Harness: 输入净化 + 安全校验
    sanitized_input, injection_issues = _safety_gate.sanitize_input(req.message.strip())
    if injection_issues:
        AuditLogger.log(
            "input_sanitized",
            {"original_length": len(req.message), "issues": injection_issues},
            user_id,
        )

    # Use existing conversation_id from request, or generate a new one
    conversation_id = (
        req.conversation_id or f"conv_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    )

    state = create_initial_state(
        sanitized_input, user_id, conversation_id, current_time=req.current_time
    )
    state["trace_id"] = get_trace_id()  # 全链路追踪 ID 注入到 LangGraph state

    # Load conversation context: recent messages + summary for long conversations
    try:
        ctx = await db.get_conversation_context(conversation_id)
        summary = ctx.get("summary")
        recent = ctx.get("recent_messages", [])
        if recent and not summary:
            # Short conversation: include last messages with timestamps for temporal awareness
            history_lines = []
            for m in recent[-20:]:
                role_label = (
                    "用户"
                    if m.get("role") == "user"
                    else "助手"
                    if m.get("role") == "assistant"
                    else "Agent"
                )
                content = (m.get("content") or "")[:200]
                ts = (m.get("created_at") or "")[:16]  # YYYY-MM-DD HH:MM
                history_lines.append(f"[{ts}][{role_label}]: {content}")
            state["history_summary"] = (
                "近期对话（带时间戳，最近的在最后）:\n" + "\n".join(history_lines)
            )
        elif summary:
            # Long conversation: summary + last few messages with timestamps
            history_lines = [f"EARLIER对话摘要（非本次会话的早期内容）: {summary}"]
            if recent:
                history_lines.append("最近对话（带时间戳）:")
                for m in recent[-10:]:
                    role_label = (
                        "用户"
                        if m.get("role") == "user"
                        else "助手"
                        if m.get("role") == "assistant"
                        else "Agent"
                    )
                    content = (m.get("content") or "")[:200]
                    ts = (m.get("created_at") or "")[:16]
                    history_lines.append(f"[{ts}][{role_label}]: {content}")
            state["history_summary"] = "\n".join(history_lines)
    except Exception:
        logger.warning("流式会话上下文加载失败", exc_info=True)

    if req.execute_trade:
        state["trade_side"] = req.trade_side or "BUY"
        state["trade_quantity"] = req.trade_quantity or 100

    async def event_generator():
        agent_logs = []
        silent_logs = []  # chief_strategist logs (hidden from user, but kept for state)
        yielded_agents = set()  # deduplication: agent+timestamp already streamed
        # Accumulate full state across all node updates (astream yields per-node deltas)
        accumulated_state = dict(state)
        _saved = False  # 会话是否已持久化（避免断连/异常时重复保存）

        async def _persist_partial(note: str):
            """断连/异常时尽力保存已流出的部分上下文，避免整轮会话丢失"""
            if _saved:
                return
            try:
                partial = dict(accumulated_state)
                partial.setdefault("agent_logs", agent_logs)
                partial["intent"] = partial.get("intent") or "chat"
                if not partial.get("final_response"):
                    partial["final_response"] = note
                await _save_conversation(conversation_id, user_id, req.message, partial)
            except Exception:
                logger.warning("中断会话保存失败", exc_info=True)

        try:
            # Step 1: Run graph — collect agent_logs from each node
            async for chunk in trading_graph.astream(state, stream_mode="updates"):
                for node_update in chunk.values():
                    # Merge node update: agent_logs uses last-writer-wins (no reducer),
                    # messages uses operator.add (extend)
                    for key, value in node_update.items():
                        if key == "messages" and isinstance(value, list):
                            accumulated_state.setdefault(key, []).extend(value)
                        elif key == "agent_logs" and isinstance(value, list):
                            accumulated_state[key] = (
                                value  # overwrite: full state, not delta
                            )
                        else:
                            accumulated_state[key] = value
                    logs = node_update.get("agent_logs", [])
                    if isinstance(logs, list) and logs:
                        for log in logs:
                            # Deduplication: skip logs already yielded from a previous node update
                            log_key = (log.get("agent"), log.get("timestamp"))
                            if log_key in yielded_agents:
                                continue

                            # Skip chief_strategist — its thinking is internal, not for user display
                            # Exception: allow chief_strategist through when in chat_mode (multi-agent addressing)
                            if log.get("agent") == "chief_strategist" and not log.get(
                                "is_chat_mode"
                            ):
                                silent_logs.append(log)
                                yielded_agents.add(log_key)
                                continue

                            # Agent Chat Mode: multi-message streaming
                            if log.get("is_chat_mode"):
                                chat_messages = log.get("chat_messages", [])
                                for ci, chat_msg in enumerate(chat_messages):
                                    content = chat_msg.get("content", "")
                                    is_followup = chat_msg.get("is_followup", False)

                                    # Delay before follow-up messages for natural cadence
                                    if is_followup and ci > 0:
                                        await asyncio.sleep(0.3)

                                    # Send agent_log_start
                                    yield f"data: {json_mod.dumps({'type': 'agent_log_start', 'agent': log.get('agent'), 'emoji': log.get('emoji'), 'name_cn': log.get('name_cn'), 'color': log.get('color'), 'timestamp': log.get('timestamp'), 'is_followup': is_followup}, ensure_ascii=False)}\n\n"

                                    # Stream content character by character
                                    i = 0
                                    while i < len(content):
                                        chunk_size = 5 if ord(content[i]) > 127 else 9
                                        end = min(i + chunk_size, len(content))
                                        yield f"data: {json_mod.dumps({'type': 'agent_token', 'text': content[i:end]}, ensure_ascii=False)}\n\n"
                                        i = end
                                        await asyncio.sleep(0.004)

                                    yield f"data: {json_mod.dumps({'type': 'agent_log_end', 'agent': log.get('agent'), 'is_followup': is_followup}, ensure_ascii=False)}\n\n"

                                agent_logs.append(log)
                                yielded_agents.add(log_key)
                                continue

                            # Standard agent log (analysis mode)
                            content = log.get("content", "")
                            # Send agent_log_start with metadata (no content)
                            yield f"data: {json_mod.dumps({'type': 'agent_log_start', 'agent': log.get('agent'), 'emoji': log.get('emoji'), 'name_cn': log.get('name_cn'), 'color': log.get('color'), 'timestamp': log.get('timestamp')}, ensure_ascii=False)}\n\n"

                            # Stream content character by character (2-3 chars at a time for Chinese readability)
                            i = 0
                            while i < len(content):
                                chunk_size = 5 if ord(content[i]) > 127 else 9
                                end = min(i + chunk_size, len(content))
                                yield f"data: {json_mod.dumps({'type': 'agent_token', 'text': content[i:end]}, ensure_ascii=False)}\n\n"
                                i = end
                                await asyncio.sleep(
                                    0.004
                                )  # 4ms typewriter delay（程序层低开销）

                            # Agent content complete
                            yield f"data: {json_mod.dumps({'type': 'agent_log_end', 'agent': log.get('agent')}, ensure_ascii=False)}\n\n"

                            # 追加消息（增加自由度）：主消息后按概率追加一条补充消息
                            followups = log.get("followups", [])
                            for fu in followups:
                                await asyncio.sleep(0.3)
                                yield f"data: {json_mod.dumps({'type': 'agent_log_start', 'agent': log.get('agent'), 'emoji': log.get('emoji'), 'name_cn': log.get('name_cn'), 'color': log.get('color'), 'timestamp': log.get('timestamp'), 'is_followup': True}, ensure_ascii=False)}\n\n"
                                j = 0
                                while j < len(fu):
                                    chunk_size = 5 if ord(fu[j]) > 127 else 9
                                    end = min(j + chunk_size, len(fu))
                                    yield f"data: {json_mod.dumps({'type': 'agent_token', 'text': fu[j:end]}, ensure_ascii=False)}\n\n"
                                    j = end
                                    await asyncio.sleep(0.004)
                                yield f"data: {json_mod.dumps({'type': 'agent_log_end', 'agent': log.get('agent'), 'is_followup': True}, ensure_ascii=False)}\n\n"

                            agent_logs.append(log)
                            yielded_agents.add(log_key)
                            await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            # 客户端断连：保存已流出的部分上下文后向上抛出，让响应正常终止
            await _persist_partial("（回复因连接中断而未完成）")
            raise
        except Exception as e:
            yield f"data: {json_mod.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
            await _persist_partial("（生成过程出错，已保存部分分析）")
            return

        # Use accumulated_state (full state after all nodes) instead of last chunk's delta
        final_state = accumulated_state

        # Step 2: Generate report NON-streaming (full text for download/new-window)
        needs_report = final_state.get("needs_report", False)

        response_text = ""

        # Direct Agent Chat: agent already spoke via SSE agent_log – DON'T repeat in assistant bubble
        if final_state.get("intent") == "direct_agent":
            chat_msgs = final_state.get("agent_chat_response", [])
            spoken = chat_msgs[0].get("content", "") if chat_msgs else ""
            # Store for conversation history only, NOT as final_response/response_text
            # (the frontend already rendered the agent bubble from SSE agent_token events)
            final_state["final_response"] = ""
            response_text = ""
            # Save agent response separately for conversation persistence
            final_state["agent_spoken"] = spoken
        elif not needs_report:
            # chat/watchlist 用首席快速回复；portfolio/analyze/market 基于 Agent 输出合成
            response_text = await _generate_non_report_reply(final_state)
        else:
            # Full report for analyze/trade/portfolio/query → 使用 Pro 模型深度推理
            try:
                from ..agents.prompts import RESPONSE_GENERATOR_SYSTEM
                from ..agents.response_generator import _build_context
                from ..services.llm import choose_client

                context = _build_context(final_state)
                llm_messages = [
                    {"role": "system", "content": RESPONSE_GENERATOR_SYSTEM},
                    {"role": "user", "content": context},
                ]
                response_text = await choose_client(True).chat(
                    llm_messages, temperature=0.5, max_tokens=3072
                )
            except Exception:
                logger.exception("Pro模型流式报告生成失败，尝试降级回复")
                try:
                    from ..agents.response_generator import _fallback_response

                    response_text = _fallback_response(final_state)
                except Exception:
                    logger.exception("报告生成完全失败")
                    response_text = "报告生成失败，请重试。"

        # 直接执行：用户明确说"直接买入/卖出"，跳过确认卡片，立即提交订单
        if (
            final_state.get("direct_execute")
            and final_state.get("intent") == "trade"
            and final_state.get("trade_plan")
        ):
            try:
                from .trade import create_trade

                tp = final_state["trade_plan"]
                _sym = pure_code(tp.get("symbol", ""))
                _name = (tp.get("name") or "").strip()
                _side = (tp.get("side") or "").upper()
                _qty = tp.get("suggested_quantity") or tp.get("quantity") or 100
                _price = tp.get("suggested_price") or tp.get("current_price")
                _result = await create_trade(
                    user_id=user_id,
                    symbol=_sym,
                    name=_name,
                    side=_side,
                    quantity=int(_qty),
                    price=float(_price) if _price else None,
                    order_type="LIMIT",
                )
                final_state["order_result"] = _result
                _suffix = (
                    "订单已提交，等待撮合成交"
                    if _result.get("is_trading_time")
                    else "已提交挂单，下一交易日撮合"
                )
                response_text = f"{_result['message']}。{_suffix}"
                final_state["_direct_executed"] = True
            except HTTPException as _he:
                logger.warning("直接执行交易失败: %s", _he.detail)
                final_state["_direct_executed"] = True
                response_text = f"直接下单失败：{_he.detail}"
            except Exception:
                logger.warning("直接执行交易异常，回退到确认卡片", exc_info=True)
                final_state["_direct_executed"] = False
        else:
            final_state["_direct_executed"] = False

        # Save to final_state (skip for direct_agent — already set above from agent_chat_response)
        if final_state.get("intent") != "direct_agent":
            final_state["final_response"] = _strip_markdown(response_text)

        # Save conversation (await to ensure persistence before done event)
        await _save_conversation(conversation_id, user_id, req.message, final_state)
        _saved = True

        # Step 3: Send done event with full report + metadata
        trade_plan = final_state.get("trade_plan")
        pending_action = final_state.get("pending_action")
        is_trading = final_state.get("is_trading_time", False)
        action_required = None
        if trade_plan and not final_state.get("_direct_executed"):
            suggested_price = trade_plan.get("suggested_price") or trade_plan.get(
                "current_price", 0
            )
            suggested_qty = trade_plan.get("suggested_quantity") or trade_plan.get(
                "quantity", 100
            )
            prev_close = trade_plan.get("prev_close", suggested_price)
            smart_pricing = trade_plan.get("smart_pricing", {})

            action_required = {
                "type": "trade_confirm",
                "message": "请确认交易详情，确认后立即执行"
                if is_trading
                else "当前为非交易时间，是否确认提交挂单？",
                "trade_plan": {
                    "symbol": trade_plan.get("symbol"),
                    "name": trade_plan.get("name"),
                    "side": trade_plan.get("side"),
                    "quantity": trade_plan.get("quantity", 100),
                    "price": suggested_price,
                    "prev_close": prev_close,
                    "is_trading_time": is_trading,
                    "risk_level": trade_plan.get("risk_level", "NORMAL"),
                    # Smart pricing
                    "suggested_price": suggested_price,
                    "suggested_quantity": suggested_qty,
                    "current_price": trade_plan.get("current_price", suggested_price),
                    "smart_pricing_note": smart_pricing.get("note", ""),
                    # Amount
                    "estimated_amount": round(suggested_price * suggested_qty, 2),
                    # Allow force mode
                    "force_mode_available": True,
                },
            }
        elif pending_action and isinstance(pending_action, dict):
            # 通用待确认操作：自选股增删 / 撤单（trade_confirm 仍走上面的交易分支）
            action_required = pending_action

        # 是否建议用户生成完整报告：有分析价值、已有 Agent 输出、但用户未显式要求报告
        suggest_report = (
            not final_state.get("needs_report", False)
            and final_state.get("intent", "")
            in ("analyze", "market", "portfolio", "query")
            and bool(final_state.get("agent_logs"))
        )

        result_data = {
            "type": "done",
            "intent": final_state.get("intent", "chat"),
            "needs_report": final_state.get("needs_report", False),
            "needed_agents": final_state.get("needed_agents", []),
            "active_symbol": final_state.get("active_symbol"),
            "active_name": final_state.get("active_name"),
            "trade_plan": trade_plan,
            "portfolio_summary": final_state.get("portfolio_summary"),
            "plan": final_state.get("plan"),
            "conversation_id": conversation_id,
            "action_required": action_required,
            "suggest_report": suggest_report,
        }
        if response_text:
            result_data["response"] = response_text
        yield f"data: {json_mod.dumps(result_data, ensure_ascii=False, default=str)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _error_event_generator(message: str):
    """Generate a simple SSE error event."""
    yield f"data: {json_mod.dumps({'type': 'error', 'message': message}, ensure_ascii=False)}\n\n"


async def _save_conversation(
    conv_id: str, user_id: str, message: str, final_state: dict
):
    """Persist conversation to DB (async background task)"""
    try:
        await db.create_conversation(conv_id, user_id)
        existing = await db.get_conversation_messages(conv_id)
        if not existing:
            await db.update_conversation_title(conv_id, message[:20])
        await db.save_message(conv_id, "user", message)
        for log_item in final_state.get("agent_logs", []):
            # Skip chief_strategist — not shown to user, don't persist
            if log_item.get("agent") == "chief_strategist":
                continue
            if log_item.get("is_chat_mode"):
                # Multi-message agent chat: save each message separately
                chat_msgs = log_item.get("chat_messages", [])
                for ci, chat_msg in enumerate(chat_msgs):
                    await db.save_message(
                        conv_id,
                        "agent_log",
                        chat_msg.get("content", ""),
                        agent_name=log_item.get("agent", ""),
                        agent_emoji=log_item.get("emoji", ""),
                        metadata=json_mod.dumps(
                            {
                                "is_followup": chat_msg.get("is_followup", False),
                                "index": ci,
                            }
                        ),
                    )
            else:
                await db.save_message(
                    conv_id,
                    "agent_log",
                    log_item.get("content", ""),
                    agent_name=log_item.get("agent", ""),
                    agent_emoji=log_item.get("emoji", ""),
                )
                for fu in log_item.get("followups", []):
                    await db.save_message(
                        conv_id,
                        "agent_log",
                        fu,
                        agent_name=log_item.get("agent", ""),
                        agent_emoji=log_item.get("emoji", ""),
                        metadata=json_mod.dumps({"is_followup": True}),
                    )
        # Skip assistant message for direct_agent — agent already spoke via agent_log
        if final_state.get("intent") != "direct_agent":
            await db.save_message(
                conv_id,
                "assistant",
                final_state.get("final_response", ""),
                metadata=json_mod.dumps(
                    {
                        "intent": final_state.get("intent"),
                        "active_symbol": final_state.get("active_symbol"),
                        "needs_report": final_state.get("needs_report", False),
                        "plan": final_state.get("plan"),
                    }
                ),
            )
    except Exception:
        logger.warning("流式会话保存失败", exc_info=True)


@router.post("/trade-action")
async def trade_action(
    req: TradeActionRequest, user_id: str = Depends(get_current_user)
):
    """用户确认或取消待确认操作（交易 / 自选股增删 / 撤单）"""
    if req.action not in ("confirm", "cancel"):
        raise HTTPException(400, "action must be confirm or cancel")

    action_type = (req.action_type or "").strip() or ("trade" if req.trade_plan else "")
    data = req.data or {}

    async def _save_reply(message: str, meta: dict):
        if req.conversation_id:
            try:
                await db.save_message(
                    req.conversation_id,
                    "assistant",
                    message,
                    metadata=json_mod.dumps(meta),
                )
            except Exception:
                logger.warning("确认操作消息保存失败", exc_info=True)

    # ---------- 取消 ----------
    if req.action == "cancel":
        if action_type == "cancel_order":
            perm = _safety_gate.check_operation(
                "cancel_order", user_id, authorized=True
            )
            if not perm.allowed:
                raise HTTPException(403, f"操作权限不足：{perm.reason}")
            order_ids = data.get("order_ids") or (
                [req.order_id] if req.order_id else []
            )
            cancelled = []
            for oid in order_ids:
                from ..services.order_engine import cancel_order as engine_cancel_order

                result = engine_cancel_order(str(oid), user_id=user_id)
                if not result["success"]:
                    continue
                try:
                    await db.update_trade_status_by_order_id(
                        str(oid),
                        "CANCELLED",
                        cancel_reason=result.get("order", {}).get(
                            "cancel_reason", "用户主动撤单"
                        ),
                    )
                except Exception:
                    logger.warning("撤单DB同步失败", exc_info=True)
                cancelled.append(str(oid))
            msg = f"已撤销 {len(cancelled)} 笔订单" if cancelled else "没有可撤销的订单"
            await _save_reply(
                msg, {"action": "order_cancelled", "order_ids": cancelled}
            )
            return {
                "success": bool(cancelled),
                "message": msg,
                "action": "cancelled",
                "order_ids": cancelled,
            }

        await _save_reply(
            "操作已取消", {"action": "cancelled", "action_type": action_type}
        )
        return {"success": True, "message": "操作已取消", "action": "cancelled"}

    # ---------- 确认分支：最小权限校验（写操作经用户确认后显式提权） ----------
    if action_type in _safety_gate.WRITE_OPERATIONS:
        perm = _safety_gate.check_operation(action_type, user_id, authorized=True)
        if not perm.allowed:
            raise HTTPException(403, f"操作权限不足：{perm.reason}")

    # ---------- 确认：自选股添加 ----------
    if action_type == "watchlist_add":
        from .watchlist import add_watchlist_core

        symbol = pure_code(data.get("symbol", ""))
        name = (data.get("name") or "").strip()
        result = await add_watchlist_core(user_id, symbol, name)
        await _save_reply(
            result["message"],
            {"action": "watchlist_added", "symbol": symbol, "name": name},
        )
        return {
            "success": True,
            "message": result["message"],
            "action": "watchlist_added",
            "watchlist": result.get("watchlist"),
        }

    # ---------- 确认：自选股移除 ----------
    if action_type == "watchlist_remove":
        from .watchlist import remove_watchlist_core

        symbol = pure_code(data.get("symbol", ""))
        result = await remove_watchlist_core(user_id, symbol)
        await _save_reply(
            result["message"], {"action": "watchlist_removed", "symbol": symbol}
        )
        return {
            "success": True,
            "message": result["message"],
            "action": "watchlist_removed",
            "watchlist": result.get("watchlist"),
        }

    # ---------- 确认：撤单 ----------
    if action_type == "cancel_order":
        order_ids = data.get("order_ids") or ([req.order_id] if req.order_id else [])
        cancelled = []
        errors = []
        for oid in order_ids:
            from ..services.order_engine import cancel_order as engine_cancel_order

            result = engine_cancel_order(str(oid), user_id=user_id)
            if not result["success"]:
                errors.append(result["message"])
                continue
            try:
                await db.update_trade_status_by_order_id(
                    str(oid),
                    "CANCELLED",
                    cancel_reason=result.get("order", {}).get(
                        "cancel_reason", "用户主动撤单"
                    ),
                )
            except Exception:
                logger.warning("撤单DB同步失败", exc_info=True)
            cancelled.append(str(oid))
        msg = (
            f"已撤销 {len(cancelled)} 笔订单"
            if cancelled
            else ("；".join(errors) or "没有可撤销的订单")
        )
        await _save_reply(msg, {"action": "order_cancelled", "order_ids": cancelled})
        return {
            "success": bool(cancelled),
            "message": msg,
            "action": "cancelled",
            "order_ids": cancelled,
        }

    # ---------- 确认：交易（默认） ----------
    trade_plan = req.trade_plan
    if not trade_plan:
        raise HTTPException(400, "交易计划不能为空")
    symbol = pure_code(trade_plan.get("symbol", ""))
    name = (trade_plan.get("name") or "").strip()
    side = (trade_plan.get("side") or "").upper()
    quantity = trade_plan.get("quantity") or 100
    price = (
        trade_plan.get("price")
        or trade_plan.get("current_price")
        or trade_plan.get("suggested_price")
    )

    if not symbol or side not in ("BUY", "SELL"):
        raise HTTPException(400, "交易计划缺少股票代码或方向")

    # 统一走交易核心逻辑（含余额/持仓/T+1/涨跌停校验和锁定）
    from .trade import create_trade

    result = await create_trade(
        user_id=user_id,
        symbol=symbol,
        name=name,
        side=side,
        quantity=int(quantity),
        price=float(price) if price else None,
        order_type="LIMIT",
    )

    suffix = (
        "订单已提交，等待撮合成交"
        if result["is_trading_time"]
        else "已提交挂单，下一交易日撮合"
    )
    confirm_msg = f"{result['message']}。{suffix}"
    await _save_reply(
        confirm_msg,
        {
            "action": "trade_confirmed",
            "trade_plan": trade_plan,
            "order_id": result.get("order_id"),
        },
    )
    return {
        "success": True,
        "message": confirm_msg,
        "action": "placed",
        "order_id": result["order_id"],
    }


@router.post("/report")
async def generate_report(req: ReportRequest, user_id: str = Depends(get_current_user)):
    """基于已有 Agent 分析结果，生成完整报告（用户主动触发，不重新调度 Agent）"""
    conv_id = req.conversation_id
    try:
        msgs = await db.get_conversation_messages(conv_id)
    except Exception:
        msgs = []

    user_question = ""
    agent_parts = []
    for m in msgs:
        role = m.get("role", "")
        if role == "user":
            user_question = m.get("content", "")
        elif role == "agent_log":
            content = (m.get("content") or "").strip()
            if content:
                agent_parts.append(f"【{m.get('agent_name', '')}】\n{content}")

    if not agent_parts:
        raise HTTPException(400, "暂无 Agent 分析结果，无法生成报告")

    context = (
        f"用户问题：{user_question or '（无）'}\n\n"
        f"用户明确要求生成完整分析报告，请使用 REPORT MODE 输出。\n\n"
        f"各 Agent 分析结果：\n\n" + "\n\n".join(agent_parts)
    )

    try:
        from ..agents.prompts import RESPONSE_GENERATOR_SYSTEM
        from ..services.llm import choose_client

        llm_messages = [
            {"role": "system", "content": RESPONSE_GENERATOR_SYSTEM},
            {"role": "user", "content": context},
        ]
        report = await choose_client(True).chat(
            llm_messages, temperature=0.5, max_tokens=3072
        )
    except Exception:
        logger.exception("报告生成失败")
        raise HTTPException(500, "报告生成失败，请稍后重试")

    try:
        await db.save_message(
            conv_id,
            "assistant",
            report,
            metadata=json_mod.dumps(
                {"intent": req.intent, "needs_report": True, "report_generated": True}
            ),
        )
    except Exception:
        logger.warning("报告保存失败", exc_info=True)

    return {"success": True, "report": report}


@router.get("/conversations")
async def list_conversations(user_id: str = Depends(get_current_user)):
    convs = await db.get_user_conversations(user_id)
    return {"conversations": convs}


@router.get("/conversations/{conv_id}")
async def get_conversation(conv_id: str, user_id: str = Depends(get_current_user)):
    msgs = await db.get_conversation_messages(conv_id)
    # Transform DB rows to frontend Message format
    transformed = []
    for m in msgs:
        role = m.get("role", "")
        msg = {
            "role": role,
            "content": m.get("content", ""),
            "timestamp": m.get("created_at", ""),
        }
        if role == "agent_log":
            agent_key = m.get("agent_name", "")
            agent_info = AGENT_INFO_MAP.get(
                agent_key, {"name_cn": agent_key, "color": "#60a5fa"}
            )
            msg["role"] = "agent"
            msg["metadata"] = {
                "agentLog": {
                    "agent": agent_key,
                    "emoji": m.get("agent_emoji", ""),
                    "name_cn": agent_info["name_cn"],
                    "color": agent_info["color"],
                    "content": m.get("content", ""),
                    "timestamp": m.get("created_at", ""),
                }
            }
        elif role == "assistant":
            try:
                meta = json_mod.loads(m.get("metadata", "{}"))
                msg["metadata"] = meta
            except Exception:
                msg["metadata"] = {}
        transformed.append(msg)
    return {"messages": transformed, "conversation_id": conv_id}


@router.delete("/conversations/{conv_id}")
async def delete_conversation_route(
    conv_id: str, user_id: str = Depends(get_current_user)
):
    await db.delete_conversation(conv_id)
    return {"success": True}
