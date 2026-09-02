"""持仓查询 API"""

from fastapi import APIRouter, Depends

from ..middleware.error_handler import get_current_user
from ..services import db
from ..services.data_source_manager import data_source_manager
from ..services.order_engine import (
    get_active_orders,
    get_locked_balance,
    get_locked_shares,
)
from ..services.position_service import compute_sellable
from ..services.symbol import pure_code

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


@router.get("")
async def get_portfolio(user_id: str = Depends(get_current_user)):
    """获取持仓概览"""
    positions = await db.get_all_positions(user_id)

    if not positions:
        account = await db.get_account(user_id)
        balance = float(account["balance"]) if account else 0
        active_orders = get_active_orders(user_id) or []
        return {
            "balance": round(balance, 2),
            "locked_balance": round(get_locked_balance(user_id), 2),
            "available_balance": round(
                max(balance - get_locked_balance(user_id), 0), 2
            ),
            "total_market_value": 0,
            "total_cost": 0,
            "total_pnl": 0,
            "total_pnl_pct": 0,
            # 空仓时不再透传可能过期的 account.total_assets，直接等于现金
            "total_assets": round(balance, 2),
            "positions": [],
            "position_count": 0,
            "active_orders": active_orders,
            "advice": [],
        }

    # 获取实时价格（统一用纯代码）：优先使用后台轮询缓存（收盘缓存），缺失的再实时补齐
    codes = [pure_code(p["symbol"]) for p in positions]
    prices: dict = {}
    from ..services.live_prices import get_cached_price

    for c in codes:
        cached = get_cached_price(c)
        if cached and cached.get("last_price"):
            prices[c] = {
                "price": cached.get("last_price"),
                "prev_close": cached.get("prev_close"),
                "name": cached.get("name"),
            }
    missing = [c for c in codes if c not in prices]
    if missing:
        prices.update(await data_source_manager.get_realtime(missing))

    portfolio_data = []
    total_market_value = 0.0
    total_cost = 0.0

    for pos in positions:
        symbol = pure_code(pos["symbol"])
        stock_info = prices.get(symbol)

        current_price = (
            float(stock_info.get("price", pos.get("latest_price", 0)))
            if stock_info
            else float(pos.get("latest_price", 0))
        )
        quantity = pos["quantity"]
        pos_total_cost = float(pos["total_cost"])
        market_value = round(current_price * quantity, 2)
        pnl = round(market_value - pos_total_cost, 2)
        pnl_pct = round(pnl / pos_total_cost * 100, 2) if pos_total_cost > 0 else 0

        t1_quantity, sellable_quantity = compute_sellable(pos)
        t1_restricted = t1_quantity > 0
        locked_shares = get_locked_shares(user_id, symbol)

        portfolio_data.append(
            {
                "symbol": symbol,
                "name": pos["name"],
                "quantity": quantity,
                "sellable_quantity": sellable_quantity,
                "t1_quantity": t1_quantity,
                "locked_shares": locked_shares,
                "tradable_quantity": max(sellable_quantity - locked_shares, 0),
                "avg_cost": round(float(pos["avg_cost"]), 2),
                "total_cost": round(pos_total_cost, 2),
                "current_price": current_price,
                "market_value": market_value,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "t1_restricted": t1_restricted,
            }
        )

        total_market_value += market_value
        total_cost += pos_total_cost

    total_pnl = round(total_market_value - total_cost, 2)
    total_pnl_pct = round(total_pnl / total_cost * 100, 2) if total_cost > 0 else 0

    account = await db.get_account(user_id)
    balance = float(account["balance"]) if account else 0

    # 获取活跃挂单
    active_orders = get_active_orders(user_id) or []
    locked_balance = round(get_locked_balance(user_id), 2)

    return {
        "balance": round(balance, 2),
        "locked_balance": locked_balance,
        "available_balance": round(max(balance - locked_balance, 0), 2),
        "total_market_value": round(total_market_value, 2),
        "total_cost": round(total_cost, 2),
        "total_pnl": total_pnl,
        "total_pnl_pct": total_pnl_pct,
        "total_assets": round(balance + total_market_value, 2),
        "positions": portfolio_data,
        "position_count": len(portfolio_data),
        "active_orders": active_orders,
    }


@router.get("/history")
async def get_portfolio_history(
    user_id: str = Depends(get_current_user), days: int = 30
):
    """获取持仓历史快照"""
    history = await db.get_portfolio_history(user_id, days)
    return {"history": history}


@router.get("/trades")
async def get_recent_trades(user_id: str = Depends(get_current_user), limit: int = 50):
    """获取最近交易记录"""
    trades = await db.get_recent_trades(user_id, limit)
    return {"trades": trades}
