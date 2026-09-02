"""数据库服务 - SQLite 异步操作"""

import logging
import os
from contextlib import asynccontextmanager

import aiosqlite

logger = logging.getLogger(__name__)

from ..config import DB_PATH as _cfg_db_path

DB_PATH = _cfg_db_path
SCHEMA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "database", "schema.sql"
)


async def init_db():
    """初始化数据库（启动时调用，幂等）：

    1. 基线 DDL：执行 schema.sql（全部 CREATE/INDEX ... IF NOT EXISTS，可安全重入）；
    2. 版本化迁移：由 backend/database/migrations.py 统一管理增量变更——每个版本与其
       “已应用”记录写入同一事务，失败自动回滚，杜绝“DDL 已生效但版本未记录”中间态；
    3. 种子数据：默认账户与用户画像（INSERT OR IGNORE，不覆盖用户已有改动）。
    """
    async with aiosqlite.connect(DB_PATH) as db:
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            await db.executescript(f.read())
        from ..database.migrations import run_migrations

        applied = await run_migrations(db)
        if applied:
            logger.info("数据库迁移完成: %s", ", ".join(applied))
        # 初始化默认账户（默认资金 100 万）
        from ..config import INITIAL_BALANCE

        await db.execute(
            """INSERT OR IGNORE INTO accounts (user_id, balance, total_assets)
               VALUES (?, ?, ?)""",
            ("default", INITIAL_BALANCE, INITIAL_BALANCE),
        )
        # 初始化默认用户画像（昵称/头像/风险偏好，可在设置页修改）
        await db.execute(
            """INSERT OR IGNORE INTO user_profiles
               (user_id, nickname, avatar, risk_level, risk_score, onboarding_completed)
               VALUES ('default', ?, ?, 'balanced', 0, 1)""",
            ("投资者", "blue"),
        )
        await db.commit()


async def get_db() -> aiosqlite.Connection:
    """获取数据库连接（自动启用 WAL 模式、外键与忙碌超时）"""
    db = await aiosqlite.connect(DB_PATH, timeout=10)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    # 写锁冲突时最多等待 5s（避免并发事务直接抛 database is locked）
    await db.execute("PRAGMA busy_timeout=5000")
    return db


@asynccontextmanager
async def transaction():
    """独立连接上的写事务（BEGIN IMMEDIATE）。

    适用场景：资金结算等必须"全部成功或全部失败"的读-改-写序列。
    · BEGIN IMMEDIATE 在事务开启即获取写锁：事务内读到的账户/持仓为该时刻一致快照，
      其它写事务会阻塞等待（受 busy_timeout 保护），杜绝"双花"式的读改写穿插；
    · 任一步异常自动 ROLLBACK，避免进程崩溃/异常导致的余额与持仓账目不一致；
    · 连接独立使用，不与其他异步操作共享，事务边界清晰。

    用法：
        async with db.transaction() as conn:
            # 全部读改写走 conn
            ...
    """
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        yield db
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()


# ---- 账户操作 ----


async def get_account(user_id: str = "default") -> dict | None:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM accounts WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def update_account_balance(user_id: str, balance: float, total_assets: float):
    db = await get_db()
    try:
        await db.execute(
            "UPDATE accounts SET balance = ?, total_assets = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
            (balance, total_assets, user_id),
        )
        await db.commit()
    finally:
        await db.close()


# ---- 自选股操作 ----


async def get_watchlist(user_id: str = "default") -> list[dict]:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM watchlist WHERE user_id = ? ORDER BY added_at", (user_id,)
        )
        return [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()


async def add_watchlist(user_id: str, symbol: str, name: str):
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO watchlist (user_id, symbol, name) VALUES (?, ?, ?)",
            (user_id, symbol, name),
        )
        await db.commit()
    finally:
        await db.close()


async def remove_watchlist(user_id: str, symbol: str):
    db = await get_db()
    try:
        await db.execute(
            "DELETE FROM watchlist WHERE user_id = ? AND symbol = ?", (user_id, symbol)
        )
        await db.commit()
    finally:
        await db.close()


async def is_in_watchlist(user_id: str, symbol: str) -> bool:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT 1 FROM watchlist WHERE user_id = ? AND symbol = ?",
            (user_id, symbol),
        )
        return await cursor.fetchone() is not None
    finally:
        await db.close()


# ---- 持仓操作 ----


async def get_position(user_id: str, symbol: str) -> dict | None:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM positions WHERE user_id = ? AND symbol = ?",
            (user_id, symbol),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def get_all_positions(user_id: str = "default") -> list[dict]:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM positions WHERE user_id = ? AND quantity > 0", (user_id,)
        )
        return [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()


async def upsert_position(
    user_id: str,
    symbol: str,
    name: str,
    quantity: int,
    avg_cost: float,
    total_cost: float,
    buy_date: str,
    latest_price: float | None = None,
    t1_quantity: int = 0,
    t1_date: str | None = None,
):
    db = await get_db()
    try:
        market_value = round(quantity * (latest_price or avg_cost), 2)
        unrealized_pnl = round(market_value - total_cost, 2)
        unrealized_pnl_pct = (
            round(unrealized_pnl / total_cost * 100, 2) if total_cost > 0 else 0
        )

        await db.execute(
            """INSERT INTO positions (user_id, symbol, name, quantity, avg_cost, total_cost,
               buy_date, t1_quantity, t1_date, latest_price, market_value, unrealized_pnl, unrealized_pnl_pct)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id, symbol) DO UPDATE SET
               quantity=excluded.quantity, avg_cost=excluded.avg_cost,
               total_cost=excluded.total_cost, buy_date=excluded.buy_date,
               t1_quantity=excluded.t1_quantity, t1_date=excluded.t1_date,
               latest_price=excluded.latest_price,
               market_value=excluded.market_value,
               unrealized_pnl=excluded.unrealized_pnl,
               unrealized_pnl_pct=excluded.unrealized_pnl_pct,
               updated_at=CURRENT_TIMESTAMP""",
            (
                user_id,
                symbol,
                name,
                quantity,
                avg_cost,
                total_cost,
                buy_date,
                t1_quantity,
                t1_date,
                latest_price,
                market_value,
                unrealized_pnl,
                unrealized_pnl_pct,
            ),
        )
        await db.commit()
    finally:
        await db.close()


async def delete_position(user_id: str, symbol: str):
    db = await get_db()
    try:
        await db.execute(
            "DELETE FROM positions WHERE user_id = ? AND symbol = ?", (user_id, symbol)
        )
        await db.commit()
    finally:
        await db.close()


async def update_position_prices(user_id: str, symbol: str, latest_price: float):
    """更新持仓的最新价格和市值"""
    pos = await get_position(user_id, symbol)
    if not pos or pos["quantity"] <= 0:
        return
    market_value = round(pos["quantity"] * latest_price, 2)
    unrealized_pnl = round(market_value - pos["total_cost"], 2)
    unrealized_pnl_pct = (
        round(unrealized_pnl / pos["total_cost"] * 100, 2)
        if pos["total_cost"] > 0
        else 0
    )
    db = await get_db()
    try:
        await db.execute(
            """UPDATE positions SET latest_price=?, market_value=?, unrealized_pnl=?,
               unrealized_pnl_pct=?, updated_at=CURRENT_TIMESTAMP
               WHERE user_id=? AND symbol=?""",
            (
                latest_price,
                market_value,
                unrealized_pnl,
                unrealized_pnl_pct,
                user_id,
                symbol,
            ),
        )
        await db.commit()
    finally:
        await db.close()


async def reset_t1_quantities(user_id: str | None = None):
    """每个交易日开盘时将全部持仓的 t1_quantity 清零（前一日买入已可卖）"""
    db = await get_db()
    try:
        if user_id:
            await db.execute(
                "UPDATE positions SET t1_quantity = 0, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                (user_id,),
            )
        else:
            await db.execute(
                "UPDATE positions SET t1_quantity = 0, updated_at = CURRENT_TIMESTAMP WHERE t1_quantity > 0"
            )
        await db.commit()
    finally:
        await db.close()


# ---- 交易记录操作 ----


async def insert_trade(
    user_id: str,
    symbol: str,
    name: str,
    side: str,
    order_type: str,
    quantity: int,
    price: float,
    amount: float,
    is_trading_time: bool = True,
    estimated_note: str | None = None,
    t1_restricted: bool = False,
    status: str = "FILLED",
    order_id: str | None = None,
    lock_price: float | None = None,
    realized_pnl: float | None = None,
    lock_fee: float = 0.0,
) -> int:
    """插入交易记录"""
    db = await get_db()
    try:
        cursor = await db.execute(
            """INSERT INTO trades (user_id, symbol, name, side, order_type, quantity, price,
               amount, is_trading_time, estimated_note, t1_restricted, status, order_id, lock_price, lock_fee, realized_pnl)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(order_id) DO UPDATE SET
               status = excluded.status,
               realized_pnl = excluded.realized_pnl,
               updated_at = CURRENT_TIMESTAMP""",
            (
                user_id,
                symbol,
                name,
                side,
                order_type,
                quantity,
                price,
                amount,
                is_trading_time,
                estimated_note,
                t1_restricted,
                status,
                order_id,
                lock_price if lock_price is not None else price,
                round(float(lock_fee or 0), 2),
                realized_pnl,
            ),
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def update_trade_status_by_order_id(order_id: str, status: str, **kwargs):
    """通过 order_id（订单引擎UUID）更新交易状态"""
    db = await get_db()
    try:
        fields = ["status = ?"]
        params = [status]
        if "filled_qty" in kwargs:
            fields.append("filled_qty = ?")
            params.append(kwargs["filled_qty"])
        if "filled_amount" in kwargs:
            fields.append("filled_amount = ?")
            params.append(kwargs["filled_amount"])
        if "fill_price" in kwargs:
            fields.append("fill_price = ?")
            params.append(kwargs["fill_price"])
        if "lock_price" in kwargs:
            fields.append("lock_price = ?")
            params.append(kwargs["lock_price"])
        if "cancel_reason" in kwargs:
            fields.append("cancel_reason = ?")
            params.append(kwargs["cancel_reason"])
        fields.append("updated_at = CURRENT_TIMESTAMP")
        params.append(order_id)
        await db.execute(
            f"UPDATE trades SET {', '.join(fields)} WHERE order_id = ?", params
        )
        await db.commit()
    finally:
        await db.close()


async def update_trade_realized_pnl(order_id: str, realized_pnl: float):
    """回填卖出单的已实现收益（一次性数据迁移用）"""
    db = await get_db()
    try:
        await db.execute(
            "UPDATE trades SET realized_pnl = ?, updated_at = CURRENT_TIMESTAMP WHERE order_id = ?",
            (realized_pnl, str(order_id)),
        )
        await db.commit()
    finally:
        await db.close()


async def update_trade_status(trade_id, status: str):
    """通过 id 或 order_id 更新交易状态"""
    db = await get_db()
    try:
        if isinstance(trade_id, int):
            await db.execute(
                "UPDATE trades SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (status, trade_id),
            )
        else:
            await db.execute(
                "UPDATE trades SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE order_id = ?",
                (status, str(trade_id)),
            )
        await db.commit()
    finally:
        await db.close()


# ---- 消息反馈 ----


async def upsert_feedback(
    user_id: str,
    agent: str,
    content: str,
    content_hash: str,
    feedback: str,
    conversation_id: str | None = None,
):
    """写入或更新用户对 Agent 输出的反馈（up/down）"""
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO message_feedback
               (user_id, conversation_id, agent, content, content_hash, feedback)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id, content_hash)
               DO UPDATE SET feedback = excluded.feedback, updated_at = CURRENT_TIMESTAMP""",
            (user_id, conversation_id, agent, content, content_hash, feedback),
        )
        await db.commit()
    finally:
        await db.close()


async def get_feedback(user_id: str) -> list[dict]:
    """获取用户全部反馈记录（不含 content 原文，避免过大）"""
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT content_hash, agent, feedback, conversation_id, created_at
               FROM message_feedback WHERE user_id = ? ORDER BY updated_at DESC""",
            (user_id,),
        )
        return [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()


async def delete_feedback(user_id: str, content_hash: str):
    """删除（取消）一条反馈记录"""
    db = await get_db()
    try:
        await db.execute(
            "DELETE FROM message_feedback WHERE user_id = ? AND content_hash = ?",
            (user_id, content_hash),
        )
        await db.commit()
    finally:
        await db.close()


async def get_active_orders_db(user_id: str | None = None) -> list[dict]:
    """获取所有活跃订单（PENDING / PARTIALLY_FILLED / ACCEPTED）"""
    db = await get_db()
    try:
        where = "status IN ('PENDING', 'PARTIALLY_FILLED', 'ACCEPTED')"
        params = []
        if user_id:
            where += " AND user_id = ?"
            params.append(user_id)
        cursor = await db.execute(
            f"SELECT * FROM trades WHERE {where} ORDER BY created_at ASC", params
        )
        return [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()


async def get_all_orders_db(
    user_id: str | None = None,
    status_filter: list | None = None,
    limit: int | None = None,
) -> list[dict]:
    """获取订单列表（支持状态筛选与条数上限，避免海量订单全量加载）"""
    db = await get_db()
    try:
        params = []
        clauses = []
        if user_id:
            clauses.append("user_id = ?")
            params.append(user_id)
        if status_filter:
            placeholders = ",".join("?" for _ in status_filter)
            clauses.append(f"status IN ({placeholders})")
            params.extend(status_filter)
        where = " AND ".join(clauses) if clauses else "1=1"
        sql = f"SELECT * FROM trades WHERE {where} ORDER BY created_at DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        cursor = await db.execute(sql, params)
        return [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()


async def get_recent_trades(user_id: str = "default", limit: int = 50) -> list[dict]:
    """获取最近交易记录"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM trades WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        )
        return [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()


# ── 锁定状态持久化 ──


async def save_locked_state(user_id: str, state_key: str, value: str):
    """保存锁定状态"""
    db = await get_db()
    try:
        await db.execute(
            """INSERT OR REPLACE INTO locked_state (user_id, state_key, state_value, updated_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)""",
            (user_id, state_key, value),
        )
        await db.commit()
    finally:
        await db.close()


async def get_locked_states(user_id: str) -> dict:
    """获取用户所有锁定状态"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT state_key, state_value FROM locked_state WHERE user_id = ?",
            (user_id,),
        )
        rows = await cursor.fetchall()
        result = {}
        for row in rows:
            result[row["state_key"]] = row["state_value"]
        return result
    finally:
        await db.close()


async def clear_locked_state(user_id: str, state_key: str | None = None):
    """清除锁定状态"""
    db = await get_db()
    try:
        if state_key:
            await db.execute(
                "DELETE FROM locked_state WHERE user_id = ? AND state_key = ?",
                (user_id, state_key),
            )
        else:
            await db.execute("DELETE FROM locked_state WHERE user_id = ?", (user_id,))
        await db.commit()
    finally:
        await db.close()


# ---- 持仓历史快照 ----


async def save_portfolio_snapshot(
    user_id: str,
    snapshot_date: str,
    total_market_value: float,
    total_cost: float,
    total_pnl: float,
    total_pnl_pct: float,
    position_count: int,
):
    db = await get_db()
    try:
        await db.execute(
            """INSERT OR REPLACE INTO portfolio_history
               (user_id, snapshot_date, total_market_value, total_cost, total_pnl, total_pnl_pct, position_count)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                snapshot_date,
                total_market_value,
                total_cost,
                total_pnl,
                total_pnl_pct,
                position_count,
            ),
        )
        await db.commit()
    finally:
        await db.close()


async def get_portfolio_history(user_id: str = "default", days: int = 30) -> list[dict]:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM portfolio_history WHERE user_id = ? ORDER BY snapshot_date DESC LIMIT ?",
            (user_id, days),
        )
        return [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()


# ---- 对话历史操作 ----


async def create_conversation(
    conv_id: str, user_id: str = "default", title: str = "新对话"
):
    db = await get_db()
    try:
        await db.execute(
            "INSERT OR IGNORE INTO conversations (id, user_id, title) VALUES (?, ?, ?)",
            (conv_id, user_id, title),
        )
        await db.commit()
    finally:
        await db.close()


async def update_conversation_title(conv_id: str, title: str):
    db = await get_db()
    try:
        await db.execute(
            "UPDATE conversations SET title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (title, conv_id),
        )
        await db.commit()
    finally:
        await db.close()


async def save_message(
    conv_id: str,
    role: str,
    content: str,
    agent_name: str | None = None,
    agent_emoji: str | None = None,
    metadata: str | None = None,
):
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO conversation_messages (conversation_id, role, agent_name, agent_emoji, content, metadata) VALUES (?, ?, ?, ?, ?, ?)",
            (conv_id, role, agent_name, agent_emoji, content, metadata),
        )
        await db.execute(
            "UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (conv_id,),
        )
        await db.commit()
    finally:
        await db.close()


async def get_conversation_messages(conv_id: str) -> list[dict]:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM conversation_messages WHERE conversation_id = ? ORDER BY created_at ASC",
            (conv_id,),
        )
        return [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()


async def get_user_conversations(
    user_id: str = "default", limit: int = 20
) -> list[dict]:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM conversations WHERE user_id = ? ORDER BY updated_at DESC LIMIT ?",
            (user_id, limit),
        )
        return [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()


async def delete_conversation(conv_id: str):
    db = await get_db()
    try:
        await db.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
        await db.commit()
    finally:
        await db.close()


# Conversation Memory Management

from ..config import MAX_RECENT_MESSAGES, SUMMARY_TRIM_THRESHOLD


async def get_conversation_context(conversation_id: str) -> dict:
    """
    Get conversation context with trimming.
    Returns:
      - recent_messages: list of last N messages (full content)
      - summary: string summary of older messages (or None)
      - total_messages: total count before trimming
    """
    messages = await get_conversation_messages(conversation_id)
    if not messages:
        return {"recent_messages": [], "summary": None, "total_messages": 0}

    total = len(messages)

    if total <= MAX_RECENT_MESSAGES:
        return {"recent_messages": messages, "summary": None, "total_messages": total}

    # Split: older messages get summarized, recent ones kept
    older = messages[: total - MAX_RECENT_MESSAGES]
    recent = messages[-MAX_RECENT_MESSAGES:]

    # Check if we already have a summary stored
    summary = await _get_stored_summary(conversation_id)

    # Generate new summary using DeepSeek if needed
    if not summary or len(older) > SUMMARY_TRIM_THRESHOLD:
        summary = await _generate_summary(older, summary or "")
        await _store_summary(conversation_id, summary)

    return {"recent_messages": recent, "summary": summary, "total_messages": total}


async def _get_stored_summary(conversation_id: str) -> str | None:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT summary FROM conversations WHERE id = ?", (conversation_id,)
        )
        row = await cursor.fetchone()
        return row["summary"] if row and row["summary"] else None
    finally:
        await db.close()


async def _store_summary(conversation_id: str, summary: str):
    db = await get_db()
    try:
        await db.execute(
            "UPDATE conversations SET summary = ? WHERE id = ?",
            (summary, conversation_id),
        )
        await db.commit()
    finally:
        await db.close()


async def _generate_summary(older_messages: list, existing_summary: str) -> str:
    """Use DeepSeek to generate a concise summary of older conversation messages.

    L4 修复：覆盖全部旧消息（而非仅最后 15 条），避免早期关键信息丢失。
    每条消息截断至 150 字符以控制 token 预算。
    """
    try:
        from ..services.llm import deepseek

        # Build conversation text（覆盖全部旧消息，保证信息不丢失）
        lines = ["Conversation history to summarize:"]
        if existing_summary:
            lines.append(f"Previous summary: {existing_summary}")
            lines.append("New messages since then:")

        for m in older_messages:
            role = m.get("role", "user")
            content = m.get("content", "")[:150]
            lines.append(f"[{role}] {content}")

        text = "\n".join(lines)

        messages = [
            {
                "role": "system",
                "content": "You are a conversation summarizer. Summarize the key topics, decisions, and context from this conversation history in Chinese. Keep it under 300 characters. Focus on: stocks discussed, analysis performed, trades executed, and current state.",
            },
            {"role": "user", "content": text},
        ]

        result = await deepseek.chat(messages, temperature=0.1, max_tokens=300)
        return result.strip() or "No key context to summarize."
    except Exception:
        logger.warning("LLM摘要失败，使用关键词降级", exc_info=True)
        # Fallback: simple text-based summary
        topics = set()
        for m in older_messages:
            content = m.get("content", "")
            for keyword in [
                "茅台",
                "平安",
                "招商",
                "宁德",
                "比亚迪",
                "五粮液",
                "五洲",
                "万科",
                "分析",
                "买入",
                "卖出",
                "持仓",
                "交易",
                "撤单",
                "行情",
                "自选",
            ]:
                if keyword in content:
                    topics.add(keyword)
        return f"讨论过的主题: {', '.join(topics) if topics else '对话历史'}"


# ---- Agent Memory ----


async def save_agent_memory(
    user_id: str,
    agent_key: str,
    symbol: str,
    query_hash: str,
    result: str,
    expires_at: str,
    query: str | None = None,
    embedding: str | None = None,
) -> None:
    """INSERT OR REPLACE agent memory entry"""
    db = await get_db()
    try:
        await db.execute(
            """INSERT OR REPLACE INTO agent_memory
               (user_id, agent_key, symbol, query_hash, query, embedding, result, expires_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            (
                user_id,
                agent_key,
                symbol,
                query_hash,
                query,
                embedding,
                result,
                expires_at,
            ),
        )
        await db.commit()
    finally:
        await db.close()


async def refresh_agent_memory(
    memory_id: int,
    result: str,
    expires_at: str,
    query: str | None = None,
    embedding: str | None = None,
) -> None:
    """刷新一条既有记忆（跨会话去重命中时更新结果与 TTL，避免重复堆积）。"""
    db = await get_db()
    try:
        await db.execute(
            """UPDATE agent_memory
               SET result = ?, expires_at = ?, query = COALESCE(?, query),
                   embedding = COALESCE(?, embedding), created_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (result, expires_at, query, embedding, memory_id),
        )
        await db.commit()
    finally:
        await db.close()


async def list_agent_memory(
    user_id: str, agent_key: str, symbol: str | None = None, limit: int = 20
) -> list[dict]:
    """列出某 agent 的近期有效记忆（用于语义检索候选集）"""
    db = await get_db()
    try:
        if symbol:
            cursor = await db.execute(
                """SELECT * FROM agent_memory
                   WHERE user_id = ? AND agent_key = ? AND symbol = ?
                   AND expires_at > CURRENT_TIMESTAMP
                   ORDER BY created_at DESC LIMIT ?""",
                (user_id, agent_key, symbol, limit),
            )
        else:
            cursor = await db.execute(
                """SELECT * FROM agent_memory
                   WHERE user_id = ? AND agent_key = ?
                   AND expires_at > CURRENT_TIMESTAMP
                   ORDER BY created_at DESC LIMIT ?""",
                (user_id, agent_key, limit),
            )
        return [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()


async def get_agent_memory(
    user_id: str, agent_key: str, query_hash: str
) -> dict | None:
    """SELECT agent memory where expires_at > now; update hit_count on hit"""
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT * FROM agent_memory
               WHERE user_id = ? AND agent_key = ? AND query_hash = ?
               AND expires_at > CURRENT_TIMESTAMP
               ORDER BY created_at DESC LIMIT 1""",
            (user_id, agent_key, query_hash),
        )
        row = await cursor.fetchone()
        if row:
            # Update hit_count
            await db.execute(
                "UPDATE agent_memory SET hit_count = hit_count + 1 WHERE id = ?",
                (dict(row)["id"],),
            )
            await db.commit()
            return dict(row)
        return None
    finally:
        await db.close()


# ---- 用户画像（引导页配置） ----


async def get_user_profile(user_id: str = "default") -> dict | None:
    """读取用户画像；不存在时返回 None"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM user_profiles WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def save_user_profile(
    user_id: str,
    nickname: str = "投资者",
    avatar: str = "blue",
    risk_level: str = "balanced",
    risk_score: int = 0,
    onboarding_completed: int = 1,
) -> None:
    """写入/更新用户画像（UPSERT，保留 created_at）"""
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO user_profiles
               (user_id, nickname, avatar, risk_level, risk_score, onboarding_completed, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(user_id) DO UPDATE SET
                 nickname=excluded.nickname,
                 avatar=excluded.avatar,
                 risk_level=excluded.risk_level,
                 risk_score=excluded.risk_score,
                 onboarding_completed=excluded.onboarding_completed,
                 updated_at=CURRENT_TIMESTAMP""",
            (user_id, nickname, avatar, risk_level, risk_score, onboarding_completed),
        )
        await db.commit()
    finally:
        await db.close()


# ---- Agent 链路追踪（L5 可观测性） ----


async def record_agent_trace(
    trace_id: str,
    agent: str,
    status: str = "ok",
    duration_ms: int = 0,
    token_used: int = 0,
    intent: str = "",
    detail: str = "",
    user_id: str = "default",
    conversation_id: str = "",
) -> None:
    """持久化 Agent 执行链路追踪（非阻塞调用，失败仅告警不中断主链路）"""
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO agent_traces
               (trace_id, conversation_id, user_id, agent, intent, status, duration_ms, token_used, detail)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trace_id,
                conversation_id,
                user_id,
                agent,
                intent,
                status,
                duration_ms,
                token_used,
                detail,
            ),
        )
        await db.commit()
    except Exception:
        logger.warning("agent_trace 记录失败: agent=%s", agent, exc_info=True)
    finally:
        await db.close()


async def get_agent_traces(
    trace_id: str | None = None, agent: str | None = None, limit: int = 100
) -> list[dict]:
    """查询 Agent 执行链路追踪"""
    db = await get_db()
    try:
        clauses = []
        params = []
        if trace_id:
            clauses.append("trace_id = ?")
            params.append(trace_id)
        if agent:
            clauses.append("agent = ?")
            params.append(agent)
        where = " AND ".join(clauses) if clauses else "1=1"
        cursor = await db.execute(
            f"SELECT * FROM agent_traces WHERE {where} ORDER BY created_at DESC LIMIT ?",
            (*params, limit),
        )
        return [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()


async def get_metrics_summary() -> dict:
    """获取运行指标汇总（L5：各 Agent 成功率/平均耗时/token 消耗）"""
    db = await get_db()
    try:
        cursor = await db.execute("""
            SELECT agent, status, COUNT(*) as cnt, SUM(duration_ms) as sum_ms, SUM(token_used) as tokens
            FROM agent_traces GROUP BY agent, status
        """)
        rows = await cursor.fetchall()
        by_agent: dict[str, dict] = {}
        total = 0
        total_ok = 0
        total_failed = 0
        total_tokens = 0
        for r in rows:
            r = dict(r)
            agent = r["agent"]
            entry = by_agent.setdefault(
                agent,
                {
                    "total": 0,
                    "ok": 0,
                    "failed": 0,
                    "avg_ms": 0,
                    "tokens": 0,
                    "_sum_ms": 0,
                },
            )
            st = r["status"]
            entry["total"] += r["cnt"]
            entry[st] = entry.get(st, 0) + r["cnt"]
            entry["tokens"] += r["tokens"] or 0
            entry["_sum_ms"] += r["sum_ms"] or 0
            total += r["cnt"]
            total_tokens += r["tokens"] or 0
            if st == "ok":
                total_ok += r["cnt"]
            elif st == "failed":
                total_failed += r["cnt"]

        for entry in by_agent.values():
            entry["avg_ms"] = (
                round(entry["_sum_ms"] / entry["total"], 1) if entry["total"] else 0
            )
            entry.pop("_sum_ms", None)

        # 会话数 = 去重 trace_id（一次对话对应一个 trace）
        sessions = 0
        cur2 = await db.execute(
            "SELECT COUNT(DISTINCT trace_id) as n FROM agent_traces WHERE trace_id != ''"
        )
        row = await cur2.fetchone()
        if row:
            sessions = row["n"] if isinstance(row, dict) else row[0]

        return {
            "total_traces": total,
            "total_sessions": sessions,
            "avg_success_rate": (total_ok / total) if total else 0,
            "total_failed": total_failed,
            "total_tokens": total_tokens,
            "by_agent": by_agent,
            "latency_percentiles": await _compute_latency_percentiles(),
            "by_intent": await _compute_intent_breakdown(),
        }
    finally:
        await db.close()


async def _compute_latency_percentiles() -> dict:
    """计算 Agent 执行耗时的 p50 / p95 / 最大耗时（L5 可观测性增强）。"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT duration_ms FROM agent_traces WHERE duration_ms > 0 ORDER BY duration_ms"
        )
        vals = [
            r["duration_ms"] for r in await cursor.fetchall() if r["duration_ms"] > 0
        ]
        if not vals:
            return {"p50_ms": 0, "p95_ms": 0, "max_ms": 0, "samples": 0}
        n = len(vals)
        return {
            "p50_ms": vals[int(n * 0.5)],
            "p95_ms": vals[min(int(n * 0.95), n - 1)],
            "max_ms": vals[-1],
            "samples": n,
        }
    finally:
        await db.close()


async def _compute_intent_breakdown() -> dict:
    """按意图维度统计成功率与调用次数（L5：识别哪类意图最易失败）。"""
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT intent, status, COUNT(*) as cnt
               FROM agent_traces WHERE intent != '' GROUP BY intent, status"""
        )
        by_intent: dict[str, dict] = {}
        for r in await cursor.fetchall():
            r = dict(r)
            intent = r["intent"]
            entry = by_intent.setdefault(intent, {"total": 0, "ok": 0, "failed": 0})
            entry["total"] += r["cnt"]
            entry[r["status"]] = entry.get(r["status"], 0) + r["cnt"]
        for entry in by_intent.values():
            entry["success_rate"] = (
                round(entry["ok"] / entry["total"], 4) if entry["total"] else 0
            )
        return by_intent
    finally:
        await db.close()


async def record_eval_result(
    eval_type: str,
    mode: str,
    total: int,
    correct: int,
    accuracy: float,
    score: float = 0.0,
    detail: str = "",
) -> None:
    """持久化一次评估结果（确定性 eval 或 LLM-as-judge）。"""
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO eval_results (eval_type, mode, total, correct, accuracy, score, detail)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (eval_type, mode, total, correct, accuracy, score, detail),
        )
        await db.commit()
    except Exception:
        logger.warning("eval_result 记录失败: type=%s", eval_type, exc_info=True)
    finally:
        await db.close()


async def get_eval_results(eval_type: str | None = None, limit: int = 20) -> list[dict]:
    """读取历史评估结果（按时间倒序）。"""
    db = await get_db()
    try:
        if eval_type:
            cursor = await db.execute(
                "SELECT * FROM eval_results WHERE eval_type = ? ORDER BY created_at DESC LIMIT ?",
                (eval_type, limit),
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM eval_results ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        return [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()


# ---- 定时任务调度（Agent 制定并调度的计划任务） ----


async def create_scheduled_task(
    user_id: str,
    name: str,
    agent_key: str,
    prompt: str,
    schedule_type: str = "interval",
    interval_seconds: int = 3600,
    daily_time: str | None = None,
    next_run_at: str | None = None,
) -> dict:
    """创建定时任务，返回完整记录"""
    db = await get_db()
    try:
        cursor = await db.execute(
            """INSERT INTO scheduled_tasks
               (user_id, name, agent_key, prompt, schedule_type, interval_seconds, daily_time, next_run_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                name,
                agent_key,
                prompt,
                schedule_type,
                interval_seconds,
                daily_time,
                next_run_at,
            ),
        )
        await db.commit()
        task_id = cursor.lastrowid
        cur = await db.execute("SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,))
        row = await cur.fetchone()
        return dict(row) if row else {}
    finally:
        await db.close()


async def list_scheduled_tasks(user_id: str = "default") -> list[dict]:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM scheduled_tasks WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )
        return [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()


async def get_scheduled_task(task_id: int, user_id: str = "default") -> dict | None:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM scheduled_tasks WHERE id = ? AND user_id = ?",
            (task_id, user_id),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def update_scheduled_task(
    task_id: int, user_id: str = "default", **fields
) -> bool:
    """按字段更新定时任务（白名单字段，防 SQL 注入）"""
    allowed = {
        "name",
        "agent_key",
        "prompt",
        "schedule_type",
        "interval_seconds",
        "daily_time",
        "status",
        "last_run_at",
        "next_run_at",
        "last_result",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    db = await get_db()
    try:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        params = list(updates.values()) + [task_id, user_id]
        cursor = await db.execute(
            f"UPDATE scheduled_tasks SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
            params,
        )
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def delete_scheduled_task(task_id: int, user_id: str = "default") -> bool:
    db = await get_db()
    try:
        cursor = await db.execute(
            "DELETE FROM scheduled_tasks WHERE id = ? AND user_id = ?",
            (task_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def get_due_scheduled_tasks(now_iso: str | None = None) -> list[dict]:
    """获取到期应执行的任务（active 且 next_run_at <= now，或从未运行）"""
    db = await get_db()
    try:
        if not now_iso:
            # 调度语义与调度器/API 一致：统一北京时间 naive 时钟
            from datetime import datetime

            from .trading_time import CHINA_TZ

            now_iso = (
                datetime.now(CHINA_TZ)
                .replace(tzinfo=None)
                .isoformat(timespec="seconds")
            )
        cursor = await db.execute(
            """SELECT * FROM scheduled_tasks
               WHERE status = 'active' AND (next_run_at IS NULL OR next_run_at <= ?)
               ORDER BY created_at ASC""",
            (now_iso,),
        )
        return [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()
