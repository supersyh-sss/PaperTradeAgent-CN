"""券商级订单撮合引擎 - 挂单/撤单/撮合成交/部分成交/日内有效 + 持久化

核心设计原则：
1. 订单当日有效，跨日自动失效
2. 市价单受连续竞价价格笼子（±2%）保护
3. 限价单支持部分成交，撮合价以委托价计
4. 资金/持仓锁定按“未成交部分”精确管理
5. 内存订单簿与 DB 通过异步任务同步，失败不阻塞主流程
"""
import asyncio
import uuid
import logging
from datetime import datetime, date, time, timedelta, timezone
from typing import Dict, List, Optional
from collections import defaultdict

from .symbol import pure_code
from .trading_time import TradingTimeChecker, AuctionPhase

BJT = timezone(timedelta(hours=8))
logger = logging.getLogger(__name__)

# ── 全局订单簿（内存） ──
# order_id -> order dict
_orders: Dict[str, dict] = {}
# symbol -> set(order_id)
_symbol_orders: Dict[str, set] = defaultdict(set)

# 锁：维护订单簿时的扣款/持仓锁定记录
# user_id -> 已锁定资金（买入委托按委托价*数量锁定）
_locked_balances: Dict[str, float] = defaultdict(float)
# user_id:symbol -> 已锁定股数
_locked_positions: Dict[str, int] = defaultdict(int)

# DB 同步开关
_db_sync_enabled = False

# 订单状态变化回调（SSE 推送等）
_status_callbacks: List[callable] = []

# 记录已执行过收盘清理的日期，避免重复清理
_last_auto_cancel_date: Optional[date] = None


def register_status_callback(cb: callable):
    """注册订单状态变化回调：fn(order: dict)"""
    _status_callbacks.append(cb)


def _notify_status_change(order: dict):
    """通知所有注册的回调"""
    for cb in _status_callbacks:
        try:
            cb(order)
        except Exception:
            logger.warning("订单状态回调失败: %s", exc_info=True)


# ── Persistence helpers ──

async def _sync_to_db(order_update: dict):
    """异步将订单变更同步到 DB"""
    if not _db_sync_enabled:
        return
    try:
        from . import db
        oid = order_update.get("order_id", "")
        status = order_update.get("status", "")
        await db.update_trade_status_by_order_id(
            oid, status,
            filled_qty=order_update.get("filled_qty"),
            filled_amount=order_update.get("filled_amount"),
            fill_price=order_update.get("fill_price"),
            cancel_reason=order_update.get("cancel_reason"),
        )
    except Exception as e:
        logger.warning(f"订单同步DB失败 ({order_update.get('order_id')}): {e}")


async def _sync_locked_state(user_id: str):
    """将锁定状态持久化到 DB"""
    if not _db_sync_enabled:
        return
    try:
        from . import db
        import json
        locked_bal = str(round(_locked_balances.get(user_id, 0), 2))
        locked_pos = json.dumps({k: v for k, v in _locked_positions.items() if k.startswith(f"{user_id}:")})
        await db.save_locked_state(user_id, "locked_balance", locked_bal)
        await db.save_locked_state(user_id, "locked_positions", locked_pos)
    except Exception as e:
        logger.warning(f"锁定状态同步DB失败: {e}")


# ── Order lifecycle ──

def _bj_today() -> date:
    return datetime.now(BJT).date()


def _is_trading_time() -> bool:
    """当前是否在 A股交易时段内"""
    return TradingTimeChecker.is_trading_time()


def _price_cage_limit(current_price: float, prev_close: float, side: str) -> float:
    """连续竞价价格笼子：买入不得高于基准价 102%，卖出不得低于 98%。
    简化以 current_price 为基准。返回允许的最高/最低成交价。
    """
    if current_price <= 0:
        return 0.0
    cage = 0.02
    if side == "BUY":
        return round(current_price * (1 + cage), 2)
    else:
        return round(current_price * (1 - cage), 2)


def _matchable_qty(order: dict, current_price: float, volume: float = 0) -> int:
    """估算本周期可成交股数（100 整数倍）。

    模拟市场深度：
    - 若行情成交量充足，按成交量 5% 估算深度；否则取剩余量的 50%，但每周期最多 1000 股
    - 保证至少 100 股，且不超过剩余量
    """
    remaining = order["quantity"] - order.get("filled_qty", 0)
    if remaining <= 0:
        return 0

    depth_from_volume = int((volume or 0) * 100 * 0.05)  # volume 单位为手，1手=100股
    base_depth = min(max(depth_from_volume, 100), 1000)
    depth = min(remaining, max(base_depth, remaining // 2))

    # 向下取整到 100 的整数倍
    lots = (depth // 100) * 100
    return max(0, min(lots, remaining))


def place_order(user_id: str, symbol: str, name: str, side: str,
                quantity: int, price: float, order_type: str = "LIMIT",
                lock_price: float = None, auction: bool = False) -> dict:
    """下单。返回 {"order_id", "status", "message", "order"}。

    注意：资金/持仓锁定由调用方在下单成功后负责，本函数只校验基础规则。
    lock_price: 买入时实际锁定的价格（如市价单可能按 price*1.02 锁定），默认等于 price。
    auction: 是否为竞价委托。竞价阶段自动检测，也可显式指定。
    """
    symbol = pure_code(symbol)
    if side not in ("BUY", "SELL"):
        return _reject("交易方向无效，必须为 BUY 或 SELL")
    if quantity <= 0:
        return _reject("数量必须大于0")
    if quantity % 100 != 0:
        return _reject(f"股数必须为100的整数倍（一手=100股），当前{quantity}股")
    if price <= 0:
        return _reject("价格必须大于0")
    if not symbol or len(symbol) != 6 or not symbol.isdigit():
        return _reject("股票代码格式不正确")

    now = datetime.now(BJT)

    # ── 竞价阶段判断 ──
    auction_phase = TradingTimeChecker.get_auction_phase()
    is_auction_phase = auction_phase != AuctionPhase.CLOSED

    # 自动检测竞价委托：在 9:15-9:25 的订单自动标记为竞价模式
    if is_auction_phase and auction_phase in (AuctionPhase.AUCTION_ORDER, AuctionPhase.AUCTION_LOCKED):
        auction = True

    # 市价单仅在连续竞价时段接受
    if order_type == "MARKET":
        if is_auction_phase:
            return _reject("集合竞价时段不接受市价单，请使用限价单参与竞价")
        if not _is_trading_time():
            return _reject("市价单仅在交易时段内接受，非交易时段请使用限价单挂单")

    oid = str(uuid.uuid4())[:12]
    if _is_trading_time() or is_auction_phase:
        order_date = _bj_today().isoformat()
    else:
        order_date = TradingTimeChecker.get_next_trading_day().date().isoformat()

    # 竞价订单的初始状态
    if auction:
        status = "PENDING"  # 竞价订单待撮合
        ord_type_display = "AUCTION"
    else:
        status = "ACCEPTED"
        ord_type_display = order_type

    order = {
        "order_id": oid,
        "user_id": user_id,
        "symbol": symbol,
        "name": name,
        "side": side,
        "quantity": quantity,
        "price": round(float(price), 3),
        "lock_price": round(float(lock_price), 3) if lock_price else round(float(price), 3),
        "order_type": order_type,
        "status": status,
        "filled_qty": 0,
        "filled_amount": 0.0,
        "avg_fill_price": 0.0,
        "fill_price": None,
        "order_date": order_date,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "auction": auction,  # 标记竞价订单
    }

    # 竞价订单进入竞价簿，非竞价订单进入常规订单簿
    if auction:
        from .auction_engine import register_auction_order
        register_auction_order(order)
        # 同时记入主订单簿用于持久化和显示
        _orders[oid] = order
        _symbol_orders[symbol].add(oid)
        msg = f"竞价委托已接受：{side} {name}({symbol}) {quantity}股 @ {price}（{TradingTimeChecker.auction_active_info()['description'][:20]}...）"
    else:
        _orders[oid] = order
        _symbol_orders[symbol].add(oid)
        msg = f"订单已接受：{side} {name}({symbol}) {quantity}股 @ {price}"

    _notify_status_change(order)
    return {
        "order_id": oid,
        "status": status,
        "message": msg,
        "order": order,
        "auction": auction,
    }


def cancel_order(order_id: str, user_id: str = None) -> dict:
    """撤单。user_id 用于校验订单归属，防止横向越权。
    竞价锁定期（9:20-9:25）不可撤单。
    """
    order = _orders.get(order_id)
    if not order:
        return {"success": False, "message": "订单不存在"}
    if user_id and order.get("user_id") != user_id:
        return {"success": False, "message": "无权操作该订单"}
    if order["status"] in ("FILLED", "CANCELLED", "REJECTED"):
        return {"success": False, "message": f"订单已{order['status']}，无法撤单"}

    # ── 竞价锁定期不可撤单 ──
    if order.get("auction"):
        if not TradingTimeChecker.is_auction_cancellable():
            return {"success": False, "message": "竞价锁定期（9:20-9:25）不可撤单，请等待9:25撮合完成或9:30后操作"}

        # 从竞价簿移除
        from .auction_engine import unregister_auction_order
        unregister_auction_order(order_id, order.get("user_id", ""))

    order["status"] = "CANCELLED"
    order["updated_at"] = datetime.now(BJT).isoformat()
    order["cancel_reason"] = "用户主动撤单"

    _release_locks(order)
    _symbol_orders[order["symbol"]].discard(order_id)

    asyncio.create_task(_sync_to_db({
        "order_id": order_id,
        "status": "CANCELLED",
        "cancel_reason": "用户主动撤单",
    }))
    asyncio.create_task(_sync_locked_state(order["user_id"]))
    _notify_status_change(order)

    return {"success": True, "message": "订单已撤销", "order": order}


def get_order(order_id: str) -> Optional[dict]:
    return _orders.get(order_id)


def get_user_orders(user_id: str, status_filter: List[str] = None) -> List[dict]:
    result = [dict(o) for o in _orders.values() if o["user_id"] == user_id]
    if status_filter:
        result = [o for o in result if o["status"] in status_filter]
    result.sort(key=lambda o: o["created_at"], reverse=True)
    return result


def get_active_orders(user_id: str) -> List[dict]:
    return get_user_orders(user_id, ["ACCEPTED", "PARTIALLY_FILLED", "PENDING"])


# ── Matching Engine ──

async def match_orders(current_prices: Dict[str, dict]) -> List[dict]:
    """撮合引擎：检查所有挂单是否达到成交条件。
    
    仅在连续竞价时段撮合。竞价阶段由 auction_engine 单独处理。
    每个撮合周期调用一次 auction_engine 生命周期检查。
    """
    # ── Auction lifecycle check ──
    from .auction_engine import run_auction_match, transition_to_continuous
    try:
        auction_fills = await run_auction_match()
        if auction_fills:
            logger.info(f"竞价撮合产生 {len(auction_fills)} 笔成交")
            # 通知持仓服务更新
            for fill in auction_fills:
                _notify_status_change({
                    "order_id": fill["order_id"],
                    "status": "FILLED",
                    "symbol": fill["symbol"],
                    "side": fill["side"],
                    "filled_qty": fill["quantity"],
                    "fill_price": fill["price"],
                    "user_id": fill["user_id"],
                    "auction_matched": True,
                })
    except Exception as e:
        logger.warning(f"竞价撮合检查异常: {e}")

    # ── 9:30 过渡期结束 → 未成交竞价订单转入连续竞价 ──
    try:
        migrated = transition_to_continuous()
        if migrated:
            logger.info(f"竞价订单转入连续竞价: {migrated} 笔")
    except Exception as e:
        logger.warning(f"竞价→连续迁移异常: {e}")

    # ── 连续竞价撮合 ──
    if not TradingTimeChecker.is_trading_time():
        return []

    filled_orders = []
    today_str = _bj_today().isoformat()

    for order_id, order in list(_orders.items()):
        if order["status"] not in ("ACCEPTED", "PARTIALLY_FILLED"):
            continue

        # 跳过竞价模式订单（由 auction_engine 处理）
        if order.get("auction"):
            continue

        # 跨日订单：当日有效
        if order.get("order_date") != today_str:
            order["status"] = "CANCELLED"
            order["cancel_reason"] = "订单已过期（当日有效）"
            order["updated_at"] = datetime.now(BJT).isoformat()
            _release_locks(order)
            _symbol_orders[order["symbol"]].discard(order_id)
            asyncio.create_task(_sync_to_db({
                "order_id": order_id,
                "status": "CANCELLED",
                "cancel_reason": "订单已过期（当日有效）",
            }))
            asyncio.create_task(_sync_locked_state(order["user_id"]))
            _notify_status_change(order)
            continue

        symbol = order["symbol"]
        market_data = current_prices.get(symbol)
        if not market_data:
            # 尝试腾讯格式 key 查找 (e.g. "sz000001" from pure "000001")
            from .symbol import to_tencent_code
            tc = to_tencent_code(symbol)
            market_data = current_prices.get(tc) if tc != symbol else None
        if not market_data:
            continue

        current_price = market_data.get("last_price", 0) or market_data.get("price", 0)
        if current_price <= 0:
            continue

        side = order["side"]
        order_price = order["price"]
        remaining = order["quantity"] - order["filled_qty"]
        prev_close = market_data.get("prev_close", current_price)

        fill_qty = 0
        fill_price = 0.0

        if order["order_type"] == "LIMIT":
            if side == "BUY" and current_price <= order_price:
                fill_price = order_price
                fill_qty = _matchable_qty(order, current_price, market_data.get("volume", 0))
            elif side == "SELL" and current_price >= order_price:
                fill_price = order_price
                fill_qty = _matchable_qty(order, current_price, market_data.get("volume", 0))
        elif order["order_type"] == "MARKET":
            # 市价单受价格笼子保护
            cage_limit = _price_cage_limit(current_price, prev_close, side)
            if side == "BUY":
                fill_price = min(current_price, cage_limit)
            else:
                fill_price = max(current_price, cage_limit)
            fill_qty = _matchable_qty(order, current_price, market_data.get("volume", 0))

        if fill_qty <= 0 or fill_price <= 0:
            continue

        # 执行成交
        fill_amount = round(fill_price * fill_qty, 2)
        order["filled_qty"] += fill_qty
        order["filled_amount"] = round(order["filled_amount"] + fill_amount, 2)
        order["avg_fill_price"] = round(order["filled_amount"] / order["filled_qty"], 3)
        order["fill_price"] = fill_price
        order["last_fill_qty"] = fill_qty
        order["last_fill_price"] = fill_price
        order["updated_at"] = datetime.now(BJT).isoformat()

        if order["filled_qty"] >= order["quantity"]:
            order["status"] = "FILLED"
            order["fill_time"] = datetime.now(BJT).isoformat()
        else:
            order["status"] = "PARTIALLY_FILLED"

        # 释放本次成交对应的锁定
        _release_filled_locks(order, fill_qty, fill_price)

        fill_event = {
            "order_id": order_id,
            "symbol": symbol,
            "name": order["name"],
            "side": side,
            "quantity": fill_qty,
            "price": fill_price,
            "amount": fill_amount,
            "user_id": order["user_id"],
            "filled_at": order["updated_at"],
            "avg_fill_price": order["avg_fill_price"],
            "order_status": order["status"],
            "order_type": order["order_type"],
            "lock_price": order.get("lock_price", fill_price),
        }
        filled_orders.append(fill_event)

        asyncio.create_task(_sync_to_db({
            "order_id": order_id,
            "status": order["status"],
            "filled_qty": order["filled_qty"],
            "filled_amount": order["filled_amount"],
            "fill_price": order["avg_fill_price"],
            "cancel_reason": order.get("cancel_reason"),
        }))
        asyncio.create_task(_sync_locked_state(order["user_id"]))
        _notify_status_change(order)

    return filled_orders


def auto_cancel_day_orders():
    """收盘后自动取消所有未成交的当日订单；每天只执行一次。"""
    global _last_auto_cancel_date

    now = datetime.now(BJT)
    today = now.date()
    if not TradingTimeChecker.is_trading_day():
        return []
    if now.time() < time(15, 0):
        return []
    if _last_auto_cancel_date == today:
        return []

    _last_auto_cancel_date = today
    cancelled = []
    cancelled_users = set()
    for oid, order in list(_orders.items()):
        if order["status"] not in ("ACCEPTED", "PARTIALLY_FILLED"):
            continue
        # 仅撤销今日订单（含非交易时段挂单顺延至今日的情况）
        if order.get("order_date") != today.isoformat():
            continue
        order["status"] = "CANCELLED"
        order["updated_at"] = now.isoformat()
        order["cancel_reason"] = "收盘自动撤销（当日有效）"
        _release_locks(order)
        _symbol_orders[order["symbol"]].discard(oid)
        asyncio.create_task(_sync_to_db({
            "order_id": oid,
            "status": "CANCELLED",
            "cancel_reason": "收盘自动撤销（当日有效）",
        }))
        cancelled.append(order)
        cancelled_users.add(order["user_id"])
        _notify_status_change(order)

    for uid in cancelled_users:
        asyncio.create_task(_sync_locked_state(uid))
    return cancelled


def cancel_expired_orders_on_startup():
    """启动时清理非今日的过期订单"""
    today_str = _bj_today().isoformat()
    cancelled = []
    for oid, order in list(_orders.items()):
        if order["status"] not in ("ACCEPTED", "PARTIALLY_FILLED"):
            continue
        if order.get("order_date") != today_str:
            order["status"] = "CANCELLED"
            order["updated_at"] = datetime.now(BJT).isoformat()
            order["cancel_reason"] = "订单已过期（当日有效）"
            _release_locks(order)
            _symbol_orders[order["symbol"]].discard(oid)
            asyncio.create_task(_sync_to_db({
                "order_id": oid,
                "status": "CANCELLED",
                "cancel_reason": "订单已过期（当日有效）",
            }))
            cancelled.append(order)
            _notify_status_change(order)
    if cancelled:
        logger.info(f"启动清理过期订单: {len(cancelled)} 个")
    return cancelled


# ── Lock management ──

def lock_funds(user_id: str, amount: float) -> bool:
    """锁定买入资金"""
    _locked_balances[user_id] = round(_locked_balances.get(user_id, 0) + amount, 2)
    return True


def lock_shares(user_id: str, symbol: str, quantity: int) -> bool:
    """锁定卖出持仓"""
    key = f"{user_id}:{pure_code(symbol)}"
    _locked_positions[key] += quantity
    return True


def get_locked_balance(user_id: str) -> float:
    return round(_locked_balances.get(user_id, 0), 2)


def get_locked_shares(user_id: str, symbol: str) -> int:
    return _locked_positions.get(f"{user_id}:{pure_code(symbol)}", 0)


def _release_filled_locks(order: dict, fill_qty: int, fill_price: float):
    """释放本次成交部分对应的锁定"""
    uid = order["user_id"]
    sym = order["symbol"]
    if order["side"] == "BUY":
        # 买入按 lock_price 锁定（市价单可能多预留 2% 价格笼子）
        release_amount = round(fill_qty * order.get("lock_price", order["price"]), 2)
        _locked_balances[uid] = max(0, round(_locked_balances.get(uid, 0) - release_amount, 2))
    else:
        key = f"{uid}:{sym}"
        _locked_positions[key] = max(0, _locked_positions.get(key, 0) - fill_qty)


def _release_locks(order: dict):
    """释放订单剩余的未成交锁定（撤单/成交完毕/过期时调用）"""
    uid = order["user_id"]
    sym = order["symbol"]
    remaining_qty = order["quantity"] - order.get("filled_qty", 0)
    if remaining_qty <= 0:
        return
    if order["side"] == "BUY":
        release_amount = round(remaining_qty * order.get("lock_price", order["price"]), 2)
        _locked_balances[uid] = max(0, round(_locked_balances.get(uid, 0) - release_amount, 2))
    else:
        key = f"{uid}:{sym}"
        _locked_positions[key] = max(0, _locked_positions.get(key, 0) - remaining_qty)


# ── Restore from DB ──

async def restore_orders():
    """启动时从 DB 恢复活跃订单到内存订单簿"""
    global _db_sync_enabled
    _db_sync_enabled = True
    try:
        from . import db
        active = await db.get_active_orders_db()
        restored = 0
        for row in active:
            oid = row.get("order_id") or str(row.get("id"))
            symbol = pure_code(row.get("symbol", ""))
            # 恢复 lock_price：DB 无记录时，市价买入单按 price*1.02 重建锁定金额
            price = row.get("price", 0)
            order_type = row.get("order_type", "LIMIT")
            side = row.get("side", "BUY")
            lock_price = row.get("lock_price")
            if lock_price is None and side == "BUY" and order_type == "MARKET":
                lock_price = round(price * 1.02, 3)
            elif lock_price is None:
                lock_price = price
            order = {
                "order_id": oid,
                "user_id": row.get("user_id", "default"),
                "symbol": symbol,
                "name": row.get("name", ""),
                "side": side,
                "quantity": row.get("quantity", 100),
                "price": price,
                "lock_price": lock_price,
                "order_type": order_type,
                "status": row.get("status", "PENDING"),
                "filled_qty": row.get("filled_qty", 0),
                "filled_amount": row.get("filled_amount", 0),
                "avg_fill_price": row.get("fill_price") or 0,
                "fill_price": row.get("fill_price"),
                "order_date": row.get("order_date") or _bj_today().isoformat(),
                "created_at": row.get("created_at", ""),
                "updated_at": row.get("updated_at", ""),
            }
            _orders[oid] = order
            _symbol_orders[symbol].add(oid)
            restored += 1

        # 清理启动时的过期订单
        cancel_expired_orders_on_startup()

        # 恢复锁定状态（按用户）
        restored_users = {order["user_id"] for order in _orders.values()}
        for uid in restored_users:
            locked = await db.get_locked_states(uid)
            if "locked_balance" in locked:
                _locked_balances[uid] = float(locked["locked_balance"])
            if "locked_positions" in locked:
                import json
                for k, v in json.loads(locked["locked_positions"]).items():
                    _locked_positions[k] = int(v)

        logger.info(f"订单簿恢复完成: {restored} 个活跃订单, 用户: {restored_users}")
        return restored
    except Exception as e:
        logger.warning(f"订单簿恢复失败: {e}")
        _db_sync_enabled = False
        return 0


# ── Utilities ──

def _reject(msg: str) -> dict:
    return {"order_id": "", "status": "REJECTED", "message": msg}


def get_order_book_snapshot(symbol: str = None) -> dict:
    if symbol:
        oids = _symbol_orders.get(symbol, set())
        orders = [_orders[oid] for oid in oids if oid in _orders]
    else:
        orders = list(_orders.values())

    buy_orders = sorted(
        [o for o in orders if o["side"] == "BUY" and o["status"] in ("ACCEPTED", "PARTIALLY_FILLED")],
        key=lambda o: -o["price"]
    )
    sell_orders = sorted(
        [o for o in orders if o["side"] == "SELL" and o["status"] in ("ACCEPTED", "PARTIALLY_FILLED")],
        key=lambda o: o["price"]
    )

    return {
        "buy_orders": buy_orders,
        "sell_orders": sell_orders,
        "total_pending": len(buy_orders) + len(sell_orders),
    }
