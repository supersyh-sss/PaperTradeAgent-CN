"""交易执行 API + 订单引擎"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..middleware.error_handler import get_current_user
from ..services import db
from ..services.data_source_manager import data_source_manager
from ..services.live_prices import subscribe_order_stream
from ..services.order_engine import (
    cancel_order,
    get_active_orders,
    get_locked_balance,
    get_locked_shares,
    get_user_orders,
    lock_funds,
    lock_shares,
    place_order,
)
from ..services.position_service import compute_sellable
from ..services.symbol import exchange_prefix, pure_code
from ..services.trading_time import TradingTimeChecker

router = APIRouter(prefix="/api/trade", tags=["trade"])

# 下单受理互斥锁（按用户）：保证“余额/可卖校验 → place_order → 锁定 → 入库”原子执行，
# 避免两个并发请求同时通过可用余额预检后各自锁定，造成累计锁定金额透支（含手续费）。
_user_locks: dict[str, asyncio.Lock] = {}
_user_locks_guard = asyncio.Lock()


async def _get_user_lock(user_id: str) -> asyncio.Lock:
    async with _user_locks_guard:
        lock = _user_locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            _user_locks[user_id] = lock
        return lock


class TradeRequest(BaseModel):
    symbol: str
    name: str
    side: str  # BUY | SELL
    quantity: int
    price: float | None = None  # None=市价单
    order_type: str = "LIMIT"  # LIMIT | MARKET


class CancelRequest(BaseModel):
    order_id: str


async def create_trade(
    user_id: str,
    symbol: str,
    name: str,
    side: str,
    quantity: int,
    price: float | None = None,
    order_type: str = "LIMIT",
) -> dict:
    """统一的下单核心逻辑，供 /api/trade 和 chat trade-action 共用。

    返回 dict 包含 order_id/status/message 等；失败时抛出 HTTPException。
    """
    symbol = pure_code(symbol)
    name = name.strip()
    side = side.upper()
    order_type = order_type.upper()

    if not symbol or len(symbol) != 6 or not symbol.isdigit():
        raise HTTPException(400, "股票代码格式不正确")
    if side not in ("BUY", "SELL"):
        raise HTTPException(400, "交易方向必须为 BUY 或 SELL")
    if quantity <= 0:
        raise HTTPException(400, "交易数量必须大于0")
    if quantity % 100 != 0:
        raise HTTPException(400, "A股必须为100股整数倍")
    if order_type not in ("LIMIT", "MARKET"):
        raise HTTPException(400, "订单类型必须为 LIMIT 或 MARKET")

    # 获取实时价格（非交易时间使用缓存或用户指定价格）
    from ..services.live_prices import get_cached_price

    stock_info = get_cached_price(symbol) or {}
    current_price = stock_info.get("last_price", 0) or 0
    prev_close = stock_info.get("prev_close", 0) or 0

    if current_price <= 0:
        # 缓存未命中时尝试实时API
        try:
            api_prices = await data_source_manager.get_realtime([symbol])
            api_info = api_prices.get(symbol)
            if api_info:
                current_price = api_info.get("price", 0) or api_info.get(
                    "last_price", 0
                )
                prev_close = api_info.get("prev_close", 0) or prev_close
        except Exception:
            pass

    if not TradingTimeChecker.is_trading_time():
        # 非交易时间：使用用户指定价格或缓存价格
        if current_price <= 0 and order_type == "LIMIT" and price:
            current_price = price
            prev_close = price
        elif current_price <= 0:
            raise HTTPException(
                404, "非交易时间无法获取实时行情，请使用限价单并指定价格"
            )
    elif current_price <= 0:
        raise HTTPException(404, "未获取到股票行情数据")

    if order_type == "LIMIT" and price is None:
        raise HTTPException(400, "限价单必须指定价格")

    if price is None or order_type == "MARKET":
        price = current_price
        order_type = "MARKET"
    else:
        price = round(float(price), 3)
        order_type = "LIMIT"

    from ..services.trade_rules import get_price_limit

    # 涨跌停校验
    limit = get_price_limit(symbol)
    limit_up = round(prev_close * (1 + limit), 2)
    limit_down = round(prev_close * (1 - limit), 2)
    if price > limit_up:
        raise HTTPException(400, f"价格{price}超过涨停价{limit_up}")
    if price < limit_down:
        raise HTTPException(400, f"价格{price}低于跌停价{limit_down}")

    # ── 受理关键区：同一用户串行执行，保证“校验→受理→锁定→入库”原子 ──
    async with await _get_user_lock(user_id):
        # T+1 卖出检查：统一使用 position_service 计算可卖数量
        if side == "SELL":
            pos = await db.get_position(user_id, symbol)
            if not pos or pos["quantity"] <= 0:
                raise HTTPException(400, "没有该股票的持仓")
            t1_qty, sellable = compute_sellable(pos)
            available = sellable - get_locked_shares(user_id, symbol)
            if available < quantity:
                raise HTTPException(
                    400,
                    f"可卖持仓不足。需要{quantity}股，可用{available}股"
                    f"（T+1冻结{t1_qty}股，挂单冻结{get_locked_shares(user_id, symbol)}股）",
                )

        # 余额检查（市价单预留 2% 价格笼子空间）
        lock_price = price
        if side == "BUY" and order_type == "MARKET":
            lock_price = round(price * 1.02, 3)

        # 预估手续费：锁定金额 = 成交金额 + 预估手续费，防止多单并发结算时费用超支
        estimated_fee = 0.0
        if side == "BUY":
            amount = round(lock_price * quantity, 2)
            try:
                from ..services.fee_calculator import calculate_fee

                estimated_fee, _ = calculate_fee(amount, "BUY", exchange_prefix(symbol))
            except Exception:
                estimated_fee = 0.0
        lock_total = round(lock_price * quantity + estimated_fee, 2)

        if side == "BUY":
            account = await db.get_account(user_id)
            if not account:
                raise HTTPException(404, "账户不存在")
            available_balance = account["balance"] - get_locked_balance(user_id)
            if available_balance < lock_total:
                raise HTTPException(
                    400,
                    f"余额不足。需要{lock_total:.2f}（含预估手续费{estimated_fee:.2f}），"
                    f"可用{available_balance:.2f}（含已锁定{get_locked_balance(user_id):.2f}）",
                )

        # 通过订单引擎下单
        result = place_order(
            user_id,
            symbol,
            name,
            side,
            quantity,
            price,
            order_type,
            lock_price=lock_price,
            lock_fee=estimated_fee,
        )
        if result["status"] == "REJECTED":
            raise HTTPException(400, result["message"])

        order_id = result["order_id"]

        # 锁定资金/持仓（锁定总额含预估手续费）
        if side == "BUY":
            lock_funds(user_id, lock_total)
        else:
            lock_shares(user_id, symbol, quantity)

        # 入库；若入库失败则撤单并释放锁定，避免订单簿与 DB 不一致
        try:
            await db.insert_trade(
                user_id,
                symbol,
                name,
                side,
                order_type,
                quantity,
                price,
                round(price * quantity, 2),
                is_trading_time=TradingTimeChecker.is_trading_time(),
                t1_restricted=(side == "BUY"),
                status=result["status"] if result.get("auction") else "ACCEPTED",
                order_id=order_id,
                lock_price=lock_price,
                lock_fee=estimated_fee,
            )
        except Exception as exc:
            cancel_order(order_id)
            raise HTTPException(500, f"订单持久化失败，已自动撤单：{exc}")

    return {
        "success": True,
        "order_id": order_id,
        "status": result["status"],
        "message": result["message"],
        "symbol": symbol,
        "name": name,
        "side": side,
        "quantity": quantity,
        "price": price,
        "lock_price": lock_price if side == "BUY" and order_type == "MARKET" else price,
        "amount": round(price * quantity, 2),
        "order_type": order_type,
        "is_trading_time": TradingTimeChecker.is_trading_time(),
    }


@router.post("")
async def execute_trade(req: TradeRequest, user_id: str = Depends(get_current_user)):
    """通过订单引擎下单（限价单/市价单，支持撮合/撤单/部分成交）"""
    return await create_trade(
        user_id, req.symbol, req.name, req.side, req.quantity, req.price, req.order_type
    )


@router.post("/cancel")
async def cancel_trade(req: CancelRequest, user_id: str = Depends(get_current_user)):
    """撤单（带用户归属校验）"""
    result = cancel_order(req.order_id, user_id=user_id)
    if not result["success"]:
        raise HTTPException(400, result["message"])
    # 同步更新DB
    try:
        await db.update_trade_status_by_order_id(
            req.order_id,
            "CANCELLED",
            cancel_reason=result["order"].get("cancel_reason", "用户主动撤单"),
        )
    except Exception:
        pass
    return {"success": True, "message": "订单已撤销", "order": result["order"]}


@router.post("/cancel-order")
async def cancel_order_endpoint(
    req: CancelRequest, user_id: str = Depends(get_current_user)
):
    """撤单（POST /api/trade/cancel-order）- 带用户归属校验"""
    result = cancel_order(req.order_id, user_id=user_id)
    if not result["success"]:
        raise HTTPException(400, result["message"])
    try:
        await db.update_trade_status_by_order_id(
            req.order_id,
            "CANCELLED",
            cancel_reason=result["order"].get("cancel_reason", "用户主动撤单"),
        )
    except Exception:
        pass
    return {"success": True, "message": "订单已撤销", "order": result["order"]}


@router.get("/orders")
async def list_orders(
    user_id: str = Depends(get_current_user), status: str = Query(None)
):
    """查询用户订单（内存订单簿 + DB 历史订单合并）"""
    status_list = status.split(",") if status else None
    memory_orders = get_user_orders(user_id, status_list)
    memory_ids = {o["order_id"] for o in memory_orders}

    # 合并 DB 中的历史订单（非活跃状态）
    db_orders = await db.get_all_orders_db(user_id, status_filter=status_list)
    merged = memory_orders + [
        o for o in db_orders if o.get("order_id") not in memory_ids
    ]
    merged.sort(key=lambda o: o.get("created_at", ""), reverse=True)

    return {
        "data": merged,
        "locked_balance": get_locked_balance(user_id),
        "locked_shares": {
            s: get_locked_shares(user_id, s)
            for s in {o.get("symbol") for o in merged if o.get("symbol")}
        },
    }


@router.get("/orders/active")
async def active_orders(user_id: str = Depends(get_current_user)):
    """查询活跃订单（ACCEPTED/PARTIALLY_FILLED）"""
    orders = get_active_orders(user_id)
    return {"data": orders, "count": len(orders)}


@router.get("/orders/symbol/{symbol}")
async def orders_by_symbol(symbol: str, user_id: str = Depends(get_current_user)):
    """获取指定股票的全部订单（成交/撤单/已接受），用于K线图显示买卖标记"""
    symbol = pure_code(symbol)

    # 合并内存订单簿 + DB 历史订单
    memory_orders = get_user_orders(user_id)  # 所有状态
    memory_for_symbol = [
        o for o in memory_orders if pure_code(o.get("symbol", "")) == symbol
    ]

    memory_ids = {o["order_id"] for o in memory_for_symbol}
    db_orders = await db.get_all_orders_db(user_id)
    db_for_symbol = [
        o
        for o in db_orders
        if pure_code(o.get("symbol", "")) == symbol
        and o.get("order_id") not in memory_ids
    ]

    merged = memory_for_symbol + db_for_symbol
    merged.sort(key=lambda o: o.get("created_at", ""), reverse=False)

    return {"data": merged, "symbol": symbol}


@router.get("/orders/stream")
async def order_stream(user_id: str = Depends(get_current_user)):
    """SSE 端点：实时推送订单状态变化（成交/撤单/部分成交）"""
    return StreamingResponse(
        subscribe_order_stream(user_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
