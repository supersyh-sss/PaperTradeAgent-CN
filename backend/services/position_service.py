"""持仓/资金统一结算服务

所有成交或 agent 直连交易都应通过本服务完成持仓、资金和 T+1 的更新，
避免在 main.py、trade_executor.py、trade.py 中重复实现相同逻辑。
"""
from datetime import date

from . import db
from .symbol import pure_code


def _today_str() -> str:
    return date.today().isoformat()


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

    account = await db.get_account(user_id)
    if not account:
        raise ValueError(f"账户不存在: {user_id}")

    today = _today_str()
    realized_pnl = None
    cash_delta = -amount - fee if side == "BUY" else amount - fee
    new_balance = round(account["balance"] + cash_delta, 2)
    if side == "BUY" and new_balance < 0:
        raise ValueError(f"余额不足，结算后余额为 {new_balance}")

    if side == "BUY":
        existing = await db.get_position(user_id, symbol)
        if existing and existing.get("quantity", 0) > 0:
            # 成本价计入买入手续费：总成本 = 原成本 + 本次成交金额 + 本次手续费
            new_total_cost = round(existing["total_cost"] + amount + fee, 2)
            new_quantity = existing["quantity"] + quantity
            new_avg_cost = round(new_total_cost / new_quantity, 3)
            # 仅累加“今日”冻结；昨日残留的 t1_quantity 应视为已可卖
            old_t1 = existing.get("t1_quantity", 0) if existing.get("t1_date") == today else 0
            new_t1 = old_t1 + quantity
            await db.upsert_position(
                user_id, symbol, name, new_quantity, new_avg_cost, new_total_cost,
                existing["buy_date"], price, t1_quantity=new_t1, t1_date=today
            )
        else:
            # 成本价计入买入手续费：总成本 = 成交金额 + 手续费
            total_cost = round(amount + fee, 2)
            avg_cost = round(total_cost / quantity, 3)
            await db.upsert_position(
                user_id, symbol, name, quantity, avg_cost, total_cost,
                today, price, t1_quantity=quantity, t1_date=today
            )
    else:  # SELL
        pos = await db.get_position(user_id, symbol)
        if not pos or pos.get("quantity", 0) <= 0:
            raise ValueError(f"没有持仓: {symbol}")
        if pos["quantity"] < quantity:
            raise ValueError(
                f"持仓不足。需要 {quantity} 股，持有 {pos['quantity']} 股"
            )
        # 卖出收益 = (卖出价 - 持仓均价) * 数量 - 手续费
        realized_pnl = round((price - pos["avg_cost"]) * quantity - fee, 2)
        remaining = pos["quantity"] - quantity
        new_t1 = min(pos.get("t1_quantity", 0), remaining) if remaining > 0 else 0
        if remaining > 0:
            new_total_cost = round(pos["avg_cost"] * remaining, 2)
            await db.upsert_position(
                user_id, symbol, name, remaining, pos["avg_cost"], new_total_cost,
                pos["buy_date"], price, t1_quantity=new_t1, t1_date=pos.get("t1_date")
            )
        else:
            await db.delete_position(user_id, symbol)

    # 重新计算总资产 = 余额 + 所有持仓市值
    positions = await db.get_all_positions(user_id)
    total_market_value = sum(float(p.get("market_value", 0) or 0) for p in positions)
    total_assets = round(new_balance + total_market_value, 2)
    await db.update_account_balance(user_id, new_balance, total_assets)

    # 写入交易记录
    trade_id = await db.insert_trade(
        user_id, symbol, name, side, order_type, quantity, price, amount,
        is_trading_time=(estimated_note is None),
        estimated_note=estimated_note,
        t1_restricted=(side == "BUY") if t1_restricted is None else t1_restricted,
        status="FILLED",
        order_id=order_id,
        lock_price=lock_price if lock_price is not None else price,
        realized_pnl=realized_pnl,
    )

    return {
        "success": True,
        "trade_id": trade_id,
        "new_balance": new_balance,
        "total_assets": total_assets,
        "message": f"{'买入' if side == 'BUY' else '卖出'} {name}({symbol}) {quantity}股 @ {price}，金额 {amount:.2f}",
    }
