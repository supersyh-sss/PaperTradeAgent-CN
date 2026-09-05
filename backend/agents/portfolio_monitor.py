"""风控与持仓监控官 Agent"""

import json as json_mod
from datetime import datetime

from ..services import db
from ..services.agent_memory import (
    compute_query_hash,
    get_agent_memory,
    save_agent_memory,
)
from ..services.data_source_manager import data_source_manager
from ..services.llm import choose_client
from ..services.position_service import compute_sellable, today_bjt
from .prompts import AGENT_PROFILES, PORTFOLIO_MONITOR_SYSTEM
from .state import AgentState
from .utils import maybe_attach_followup


async def portfolio_monitor_node(state: AgentState) -> AgentState:
    """持仓监控节点：计算实时盈亏 + 风险评估 + LLM健康度评估"""
    user_id = state.get("user_id", "default")

    # 获取持仓
    positions = await db.get_all_positions(user_id)

    if not positions:
        account = await db.get_account(user_id)
        balance = float(account.get("balance", 0) or 0) if account else 0
        total_assets = (
            float(account.get("total_assets", 0) or 0) if account else balance
        )
        summary = {
            "balance": round(balance, 2),
            "total_market_value": 0,
            "total_cost": 0,
            "total_pnl": 0,
            "total_pnl_pct": 0,
            "total_assets": round(total_assets, 2),
            "positions": [],
            "position_count": 0,
        }
        state["portfolio_summary"] = summary

        # Empty portfolio agent_log
        agent_log = {
            "agent": "portfolio_monitor",
            "emoji": AGENT_PROFILES["portfolio_monitor"]["emoji"],
            "name_cn": AGENT_PROFILES["portfolio_monitor"]["name_cn"],
            "color": AGENT_PROFILES["portfolio_monitor"]["color"],
            "content": "持仓为空，无需监控.",
            "timestamp": datetime.now().astimezone().isoformat(),
        }
        state.setdefault("agent_logs", []).append(agent_log)
        return state

    # 获取实时价格
    codes = [p["symbol"] for p in positions]
    prices = await data_source_manager.get_realtime(codes)

    portfolio_data = []
    total_market_value = 0.0
    total_cost = 0.0

    price_updates = []
    for pos in positions:
        symbol = pos["symbol"]
        stock_info = None
        for k, v in prices.items():
            if symbol in k:
                stock_info = v
                break

        current_price = (
            float(stock_info.get("price", pos.get("latest_price", 0)))
            if stock_info
            else float(pos.get("latest_price", 0))
        )
        quantity = pos["quantity"]
        avg_cost = float(pos["avg_cost"])
        pos_total_cost = float(pos["total_cost"])
        market_value = round(current_price * quantity, 2)
        pnl = round(market_value - pos_total_cost, 2)
        pnl_pct = round(pnl / pos_total_cost * 100, 2) if pos_total_cost > 0 else 0

        # T+1 检查：统一使用 position_service 的 compute_sellable
        t1_qty, _sellable_qty = compute_sellable(pos)
        t1_restricted = t1_qty > 0

        # 收集价格刷新项，循环外单连接批量落库
        price_updates.append(
            {
                "symbol": symbol,
                "latest_price": current_price,
                "market_value": market_value,
                "unrealized_pnl": pnl,
                "unrealized_pnl_pct": pnl_pct,
            }
        )

        portfolio_data.append(
            {
                "symbol": symbol,
                "name": pos["name"],
                "quantity": quantity,
                "avg_cost": round(avg_cost, 2),
                "current_price": current_price,
                "market_value": market_value,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "t1_restricted": t1_restricted,
            }
        )

        total_market_value += market_value
        total_cost += pos_total_cost

    if price_updates:
        await db.batch_update_position_prices(user_id, price_updates)

    total_pnl = round(total_market_value - total_cost, 2)
    total_pnl_pct = round(total_pnl / total_cost * 100, 2) if total_cost > 0 else 0

    # 获取账户信息
    account = await db.get_account(user_id)
    balance = float(account.get("balance", 0) or 0) if account else 0

    # 检查是否需要策略建议
    advice = []
    if total_pnl_pct < -10:
        advice.append(
            {
                "action": "REVIEW",
                "reason": f"总亏损 {total_pnl_pct}%，超过10%，建议审视持仓",
            }
        )
    if portfolio_data:
        max_pct = (
            max(p["market_value"] / total_market_value * 100 for p in portfolio_data)
            if total_market_value > 0
            else 0
        )
        if max_pct > 50:
            advice.append(
                {
                    "action": "REDUCE",
                    "reason": f"单股集中度 {max_pct:.1f}%，建议分散风险",
                }
            )

    summary = {
        "balance": round(balance, 2),
        "total_market_value": round(total_market_value, 2),
        "total_cost": round(total_cost, 2),
        "total_pnl": total_pnl,
        "total_pnl_pct": total_pnl_pct,
        "total_assets": round(balance + total_market_value, 2),
        "positions": portfolio_data,
        "position_count": len(portfolio_data),
        "advice": advice,
    }

    state["portfolio_summary"] = summary

    # 保存快照（统一北京时间，保证与 T+1 冻结/交易结算的"日"一致）
    today = today_bjt()
    await db.save_portfolio_snapshot(
        user_id,
        today,
        round(total_market_value, 2),
        round(total_cost, 2),
        total_pnl,
        total_pnl_pct,
        len(portfolio_data),
    )

    # 更新账户总资产（乐观锁：余额被并发结算改动时跳过，避免用陈旧快照回写账目）
    if account:
        new_total = round(balance + total_market_value, 2)
        await db.update_total_assets(user_id, new_total, expect_balance=balance)

    # LLM 持仓健康度评估
    try:
        user_input = state.get("user_input", "")
        agent_key = "portfolio_monitor"
        query_hash = compute_query_hash(user_input, agent_key, "portfolio")

        cached = await get_agent_memory(
            user_id, agent_key, "portfolio", query_hash, query=user_input
        )
        llm_result = None
        if cached:
            try:
                llm_result = json_mod.loads(cached)
            except Exception:
                cached = None

        if not cached:
            portfolio_text = _build_portfolio_summary(summary)
            current_time = state.get("current_time", "")
            time_prefix = (
                f"Current system time: {current_time}\n\n" if current_time else ""
            )
            ctx_lines = [f"User asked: {user_input}"]
            history_summary = state.get("history_summary", "")
            strategy = state.get("strategy_direction", "")
            if history_summary:
                ctx_lines.append(f"Conversation context: {history_summary}")
            if strategy:
                ctx_lines.append(f"Orchestrator reasoning: {strategy}")
            ctx_prefix = "\n".join(ctx_lines) + "\n\n"
            messages = [
                {"role": "system", "content": PORTFOLIO_MONITOR_SYSTEM},
                {"role": "user", "content": ctx_prefix + time_prefix + portfolio_text},
            ]
            llm_result = await choose_client(True).chat_json(
                messages, temperature=0.1, max_tokens=1024
            )
            if llm_result.get("parse_error"):
                raise ValueError("LLM returned non-JSON")

            await save_agent_memory(
                user_id,
                agent_key,
                "portfolio",
                query_hash,
                json_mod.dumps(llm_result, ensure_ascii=False, default=str),
                query=user_input,
            )

        state["portfolio_assessment"] = llm_result
    except Exception:
        state["portfolio_assessment"] = None
        llm_result = None

    # Build concise portfolio log — personality-driven, not a full audit
    lines = ["持仓一览："]
    balance = summary.get("balance", 0)
    total_assets = summary.get("total_assets", 0)
    total_market_value = summary.get("total_market_value", 0)
    position_ratio = (
        (total_market_value / total_assets * 100) if total_assets > 0 else 0
    )
    position_label = (
        "满仓"
        if position_ratio > 95
        else "重仓"
        if position_ratio > 70
        else "半仓"
        if position_ratio > 30
        else "轻仓"
    )

    pnl_emoji = "📈" if total_pnl > 0 else "📉" if total_pnl < 0 else ""
    lines.append(
        f"总资产 {total_assets:,.2f}，{position_label}（{position_ratio:.0f}%），{pnl_emoji}累计{total_pnl:+,.2f}（{total_pnl_pct:+.2f}%）"
    )

    if portfolio_data:
        lines.append("持仓：")
        _ui = state.get("user_input", "") or ""
        _show_all = any(kw in _ui for kw in ("其余", "全部", "所有", "更多", "完整"))
        _limit = 20 if _show_all else 5
        for pd_item in portfolio_data[:_limit]:
            t1_flag = " [T+1]" if pd_item.get("t1_restricted") else ""
            pnl_sign = "+" if pd_item["pnl"] >= 0 else ""
            lines.append(
                f"  {pd_item.get('name', '')}{t1_flag} {pd_item.get('quantity', 0)}股 现价{pd_item.get('current_price', 0)} 盈亏{pnl_sign}{pd_item['pnl']:,.2f}"
            )
        if len(portfolio_data) > _limit:
            lines.append(
                f"  … 其余 {len(portfolio_data) - _limit} 只持仓（回复「查看其余持仓」继续展示）"
            )

    if llm_result:
        health = llm_result.get("portfolio_health", "")
        concentration = llm_result.get("concentration_risk", "")
        # Fallback: compute concentration locally if LLM didn't provide it
        if not concentration and portfolio_data and total_market_value > 0:
            max_pct = max(
                p["market_value"] / total_market_value * 100 for p in portfolio_data
            )
            top_name = max(portfolio_data, key=lambda p: p["market_value"]).get(
                "name", ""
            )
            concentration = (
                "分散" if max_pct < 40 else f"{max_pct:.0f}%集中在{top_name}"
            )
        if health or concentration:
            health_cn = (
                "健康"
                if health == "HEALTHY"
                else "需关注"
                if health == "CAUTION"
                else health or "正常"
            )
            lines.append(f"评估：{health_cn}，集中度{concentration}")
        flags = llm_result.get("risk_flags_chinese", [])
        if flags:
            lines.append(f"⚠ {flags[0]}")

    agent_log_content = "\n".join(lines)

    # Emit agent_log
    agent_log = {
        "agent": "portfolio_monitor",
        "emoji": AGENT_PROFILES["portfolio_monitor"]["emoji"],
        "name_cn": AGENT_PROFILES["portfolio_monitor"]["name_cn"],
        "color": AGENT_PROFILES["portfolio_monitor"]["color"],
        "content": agent_log_content,
        "timestamp": datetime.now().astimezone().isoformat(),
    }
    agent_log = maybe_attach_followup(agent_log, "portfolio_monitor")
    state.setdefault("agent_logs", []).append(agent_log)

    return state


def _build_portfolio_summary(summary: dict) -> str:
    """Build a text summary of portfolio for LLM input."""
    lines = [
        "=== Portfolio Summary ===",
        f"Balance (cash): {summary.get('balance', 0):,.2f}",
        f"Total Market Value: {summary.get('total_market_value', 0):,.2f}",
        f"Total Cost: {summary.get('total_cost', 0):,.2f}",
        f"Total PnL: {summary.get('total_pnl', 0):+,.2f}",
        f"Total PnL %: {summary.get('total_pnl_pct', 0):+.2f}%",
        f"Total Assets: {summary.get('total_assets', 0):,.2f}",
        f"Position Count: {summary.get('position_count', 0)}",
    ]
    positions = summary.get("positions", [])
    if positions:
        lines.append("--- Positions ---")
        for p in positions[:10]:
            t1 = " [T+1 restricted]" if p.get("t1_restricted") else ""
            lines.append(
                f"{p.get('name')} ({p.get('symbol')}){t1}: "
                f"qty={p.get('quantity')}, avg_cost={p.get('avg_cost')}, "
                f"price={p.get('current_price')}, mkt_val={p.get('market_value')}, "
                f"pnl={p.get('pnl'):+,.2f} ({p.get('pnl_pct'):+.2f}%)"
            )
        if len(positions) > 10:
            lines.append(f"... ({len(positions) - 10} more positions omitted)")
    advice = summary.get("advice", [])
    if advice:
        lines.append("--- Rule-Based Advice ---")
        for a in advice:
            lines.append(f"[{a.get('action')}] {a.get('reason')}")
    return "\n".join(lines)
