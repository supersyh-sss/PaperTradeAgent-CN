"""持仓/资金统一结算服务

所有成交或 agent 直连交易都应通过本服务完成持仓、资金和 T+1 的更新，
避免在 main.py、trade_executor.py、trade.py 中重复实现相同逻辑。
"""

from datetime import datetime

from . import db
from .symbol import pure_code
from .trading_time import CHINA_TZ


def _today_str() -> str:
    """统一使用北京时间（UTC+8）的日期：T+1 冻结判定必须与撮合/交易时段判断同源，
    否则服务器不在东八区时会在临近午夜/开盘边界出现冻结/解锁错位。"""
    return datetime.now(CHINA_TZ).date().isoformat()


def today_bjt() -> str:
    """对外暴露北京时间的日期字符串（Y-m-d）"""
    return _today_str()


def compute_sellable(position: dict | None) -> tuple[int, int]:
    """计算持仓的 T+1 冻结数量和可卖数量。

    以 t1_date 记录冻结买入的日期：仅当 t1_date 为今天才视为 T+1 冻结。
    兼容旧数据（无 t1_date）：回退 buy_date 判断当日买入冻结，忽略残留的
    历史 t1_quantity，避免昨日买入今日仍被误判为不可卖。
    返回 (t1_quantity, sellable_quantity)
    """
    if not position or position.get("quantity", 0) <= 0:
        return 0, 0
    quantity = position["quantity"]
    today = _today_str()
    t1_date = position.get("t1_date")
    if t1_date:
        # 只有冻结日期为今天才视为 T+1 冻结；历史日期说明已可卖
        t1_qty = position.get("t1_quantity", 0) if t1_date == today else 0
    else:
        # 旧数据（无 t1_date）：回退 buy_date 判断当日买入冻结，忽略残留的 t1_quantity
        t1_qty = quantity if position.get("buy_date") == today else 0
    t1_qty = max(0, min(int(t1_qty or 0), quantity))
    sellable = max(0, quantity - t1_qty)
    return t1_qty, sellable


async def apply_trade_fill(
    user_id: str,
    symbol: str,
    name: str,
    side: str,
    quantity: int,
    price: float,
    amount: float,
    *,
    order_type: str = "MARKET",
    order_id: str | None = None,
    estimated_note: str | None = None,
    t1_restricted: bool | None = None,
    lock_price: float | None = None,
    fee: float = 0.0,
) -> dict:
    """统一处理一次成交/交易后的资金和持仓结算。

    职责：
    - 更新账户余额（买入扣款，卖出回款）
    - 更新持仓均价、数量、T+1 冻结
    - 重新计算总资产
    - 写入交易记录

    返回值：
        {"success": True, "trade_id": int, "new_balance": float,
         "total_assets": float, "message": str}
    """
    symbol = pure_code(symbol)
    side = side.upper()
    if side not in ("BUY", "SELL"):
        raise ValueError(f"不支持的交易方向: {side}")
    if quantity <= 0:
        raise ValueError("成交数量必须大于0")
    if price <= 0 or amount <= 0:
        raise ValueError("成交价格和金额必须大于0")

    today = _today_str()
    realized_pnl = None
    trade_id = None

    # 单连接 + 单事务完成 余额/持仓/流水 的全部读改写：
    # BEGIN IMMEDIATE 使本事务持写锁，并发结算串行执行，杜绝读改写竞态（双花）；
    # 任一步失败整体回滚，杜绝“扣了钱没记录 / 持仓与余额不一致”的崩溃中间态。
    async with db.transaction() as conn:
        # 1. 读取账户（事务内一致快照）
        cur = await conn.execute("SELECT * FROM accounts WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        if row is None:
            raise ValueError(f"账户不存在: {user_id}")
        balance = round(float(row["balance"] or 0), 2)

        # 2. 资金变动（买入用条件更新做余额防御，防止任何路径下的透支）
        if side == "BUY":
            total_delta = round(amount + fee, 2)
            if balance < total_delta:
                raise ValueError(
                    f"余额不足，结算后余额为 {round(balance - total_delta, 2)}"
                )
            cur = await conn.execute(
                "UPDATE accounts SET balance = balance - ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE user_id = ? AND balance >= ?",
                (total_delta, user_id, total_delta),
            )
            if cur.rowcount == 0:
                raise ValueError(
                    f"余额不足，结算后余额为 {round(balance - total_delta, 2)}"
                )
            new_balance = round(balance - total_delta, 2)
        else:
            total_delta = round(amount - fee, 2)
            await conn.execute(
                "UPDATE accounts SET balance = balance + ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE user_id = ?",
                (total_delta, user_id),
            )
            new_balance = round(balance + total_delta, 2)

        # 3. 持仓更新（事务内读-算-写）
        if side == "BUY":
            cur = await conn.execute(
                "SELECT * FROM positions WHERE user_id = ? AND symbol = ?",
                (user_id, symbol),
            )
            existing_row = await cur.fetchone()
            existing = dict(existing_row) if existing_row else None
            if existing and existing.get("quantity", 0) > 0:
                # 成本价计入买入手续费：总成本 = 原成本 + 本次成交金额 + 本次手续费
                new_total_cost = round(float(existing["total_cost"]) + amount + fee, 2)
                new_quantity = existing["quantity"] + quantity
                new_avg_cost = round(new_total_cost / new_quantity, 3)
                # 仅累加"今日"冻结；昨日残留的 t1_quantity 应视为已可卖
                old_t1 = (
                    existing.get("t1_quantity", 0)
                    if existing.get("t1_date") == today
                    else 0
                )
                new_t1 = old_t1 + quantity
                buy_date = existing["buy_date"]
                await _upsert_position_sql(
                    conn,
                    user_id,
                    symbol,
                    name,
                    new_quantity,
                    new_avg_cost,
                    new_total_cost,
                    buy_date,
                    price,
                    t1_quantity=new_t1,
                    t1_date=today,
                )
            else:
                total_cost = round(amount + fee, 2)
                avg_cost = round(total_cost / quantity, 3)
                await _upsert_position_sql(
                    conn,
                    user_id,
                    symbol,
                    name,
                    quantity,
                    avg_cost,
                    total_cost,
                    today,
                    price,
                    t1_quantity=quantity,
                    t1_date=today,
                )
        else:  # SELL
            cur = await conn.execute(
                "SELECT * FROM positions WHERE user_id = ? AND symbol = ?",
                (user_id, symbol),
            )
            pos_row = await cur.fetchone()
            pos = dict(pos_row) if pos_row else None
            if not pos or pos.get("quantity", 0) <= 0:
                raise ValueError(f"没有持仓: {symbol}")
            if pos["quantity"] < quantity:
                raise ValueError(
                    f"持仓不足。需要 {quantity} 股，持有 {pos['quantity']} 股"
                )
            # T+1 可卖强校验：在事务快照内复核，而非信任各调用方（下单/撮合）的预读。
            # 防止并发卖出/Agent 直连成交把“今日买入冻结股”也卖出。
            t1_qty, sellable = compute_sellable(pos)
            if quantity > sellable:
                raise ValueError(
                    f"可卖数量不足：本次成交 {quantity} 股，当前可卖 {sellable} 股"
                    f"（T+1 冻结 {t1_qty} 股），违反 T+1 规则"
                )
            # 卖出收益 = (卖出价 - 持仓均价) * 数量 - 手续费
            realized_pnl = round((price - float(pos["avg_cost"])) * quantity - fee, 2)
            remaining = pos["quantity"] - quantity
            # 仅“今日”的 T+1 冻结有意义；昨日残留 t1_quantity 视为已解锁（与 compute_sellable 同口径）
            today_t1 = pos.get("t1_quantity", 0) if pos.get("t1_date") == today else 0
            new_t1 = min(today_t1, remaining) if remaining > 0 else 0
            if remaining > 0:
                new_total_cost = round(float(pos["avg_cost"]) * remaining, 2)
                await _upsert_position_sql(
                    conn,
                    user_id,
                    symbol,
                    name,
                    remaining,
                    float(pos["avg_cost"]),
                    new_total_cost,
                    pos["buy_date"],
                    price,
                    t1_quantity=new_t1,
                    t1_date=pos.get("t1_date"),
                )
            else:
                await conn.execute(
                    "DELETE FROM positions WHERE user_id = ? AND symbol = ?",
                    (user_id, symbol),
                )

        # 4. 重新计算总资产 = 余额 + 所有持仓市值
        cur = await conn.execute(
            "SELECT market_value FROM positions WHERE user_id = ? AND quantity > 0",
            (user_id,),
        )
        rows = await cur.fetchall()
        total_market_value = round(sum(float(r["market_value"] or 0) for r in rows), 2)
        total_assets = round(new_balance + total_market_value, 2)
        await conn.execute(
            "UPDATE accounts SET total_assets = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
            (total_assets, user_id),
        )

        # 5. 写入成交流水（与余额/持仓同事务）。
        #    · 存在前置委托行(order_id)：先在 Python 内读取旧累计值并计算新累计值，再显式 UPDATE。
        #      不使用 INSERT ... ON CONFLICT DO UPDATE —— SQLite 在同一语句内引用“正被本语句
        #      修改的列”时求值顺序不确定（实测部分成交第 2+ 段的 fill_price 不重新加权），
        #      改为读-算-写后结果确定且可用回归测试锁定。
        #    · 无前置行（直连/独立成交）：落一条完整成交记录（quantity=filled_qty）
        #    · 累计量达到委托量才置 FILLED，否则 PARTIALLY_FILLED —— 修复“部分成交被记成整单全额”
        #    · fee 一并落库，便于流水对账费用
        order_row = None
        if order_id:
            cur = await conn.execute(
                "SELECT id, quantity, "
                "COALESCE(filled_qty, 0) AS old_filled_qty, "
                "COALESCE(filled_amount, 0) AS old_filled_amount, "
                "COALESCE(fee, 0) AS old_fee, "
                "COALESCE(realized_pnl, 0) AS old_realized_pnl "
                "FROM trades WHERE order_id = ?",
                (order_id,),
            )
            order_row = await cur.fetchone()

        if order_row is not None:
            new_filled_qty = order_row["old_filled_qty"] + quantity
            new_filled_amount = round(order_row["old_filled_amount"] + amount, 2)
            new_fee = round(order_row["old_fee"] + fee, 2)
            new_realized_pnl = round(
                order_row["old_realized_pnl"] + (realized_pnl or 0), 2
            )
            new_fill_price = (
                round(new_filled_amount / new_filled_qty, 3)
                if new_filled_qty > 0
                else price
            )
            new_status = (
                "FILLED"
                if new_filled_qty >= order_row["quantity"]
                else "PARTIALLY_FILLED"
            )
            cur = await conn.execute(
                "UPDATE trades SET filled_qty = ?, filled_amount = ?, fee = ?, fill_price = ?, "
                "realized_pnl = ?, status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (
                    new_filled_qty,
                    new_filled_amount,
                    new_fee,
                    new_fill_price,
                    new_realized_pnl,
                    new_status,
                    order_row["id"],
                ),
            )
            trade_id = order_row["id"]
        else:
            cur = await conn.execute(
                """INSERT INTO trades (user_id, symbol, name, side, order_type, quantity, price,
                   amount, is_trading_time, estimated_note, t1_restricted, status, order_id,
                   lock_price, fee, realized_pnl, filled_qty, filled_amount, fill_price)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id,
                    symbol,
                    name,
                    side,
                    order_type,
                    quantity,
                    price,
                    amount,
                    estimated_note is None,
                    estimated_note,
                    (side == "BUY") if t1_restricted is None else t1_restricted,
                    "FILLED",
                    order_id,
                    lock_price if lock_price is not None else price,
                    round(fee, 2),
                    realized_pnl,
                    quantity,
                    amount,
                    price,
                ),
            )
            trade_id = cur.lastrowid or trade_id
            if not trade_id and order_id:
                cur2 = await conn.execute(
                    "SELECT id FROM trades WHERE order_id = ?", (order_id,)
                )
                row2 = await cur2.fetchone()
                if row2:
                    trade_id = row2["id"]

    return {
        "success": True,
        "trade_id": trade_id,
        "new_balance": new_balance,
        "total_assets": total_assets,
        "message": f"{'买入' if side == 'BUY' else '卖出'} {name}({symbol}) {quantity}股 @ {price}，金额 {amount:.2f}",
    }


async def _upsert_position_sql(
    conn,
    user_id,
    symbol,
    name,
    quantity,
    avg_cost,
    total_cost,
    buy_date,
    latest_price,
    t1_quantity=0,
    t1_date=None,
):
    """事务连接上的持仓 UPSERT（与 db.upsert_position 同语义，供单事务结算复用）"""
    market_value = round(quantity * latest_price, 2)
    unrealized_pnl = round(market_value - total_cost, 2)
    unrealized_pnl_pct = (
        round(unrealized_pnl / total_cost * 100, 2) if total_cost > 0 else 0
    )
    await conn.execute(
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
