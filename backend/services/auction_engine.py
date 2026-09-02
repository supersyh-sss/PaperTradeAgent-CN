"""集合竞价撮合引擎

A股集合竞价规则：
- 9:15-9:20：可挂单、可撤单、不撮合
- 9:20-9:25：可挂单、不可撤单、不撮合
- 9:25：撮合一次产生开盘价
- 9:25-9:30（过渡期）：可挂单、可撤单，排队等连续竞价

撮合算法：
1. 收集所有 auction 态订单
2. 对每个候选价格，计算买方累计量（≥该价的买入）和卖方累计量（≤该价的卖出）
3. 选取成交量最大的价格作为开盘价
4. 若多个价格成交量相同，选使未匹配量最小者；仍相同则取中间价
5. 低于买方出价部分按开盘价成交，高于卖方出价部分也按开盘价成交
"""

import logging
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from .trading_time import TradingTimeChecker

BJT = timezone(timedelta(hours=8))

logger = logging.getLogger(__name__)

# 竞价订单簿（内存，跨日清空）
# user_id 维度：{user_id: [order_dict, ...]}
_auction_orders: dict[str, list[dict]] = {}
# 记录上次撮合日期，避免重复
_last_auction_date: date | None = None
# 记录上次撮合时间，用于 9:25 触发一次
_last_auction_match_time: datetime | None = None
# 记录竞价→连续竞价迁移发生的日期，避免重复迁移
_last_transition_date: date | None = None

# 竞价阶段产生的开盘价缓存 {symbol: open_price}
_opening_prices: dict[str, float] = {}


def _bj_now() -> datetime:
    return datetime.now(BJT)


def _bj_today() -> date:
    return _bj_now().date()


def register_auction_order(order: dict):
    """注册竞价订单到竞价簿。order 需含 auction_mode 标记。"""
    uid = order.get("user_id", "default")
    if uid not in _auction_orders:
        _auction_orders[uid] = []
    _auction_orders[uid].append(order)
    logger.debug(
        f"竞价簿注册: {order.get('side')} {order.get('symbol')} x{order.get('quantity')} @{order.get('price')}"
    )


def unregister_auction_order(order_id: str, user_id: str):
    """从竞价簿移除订单（撤单时调用）"""
    if user_id not in _auction_orders:
        return
    _auction_orders[user_id] = [
        o for o in _auction_orders[user_id] if o.get("order_id") != order_id
    ]


def get_auction_orders_for_symbol(symbol: str) -> tuple[list[dict], list[dict]]:
    """返回指定股票的所有竞价买单和卖单
    Returns: (buy_orders, sell_orders) 未排序
    """
    buys, sells = [], []
    for orders in _auction_orders.values():
        for o in orders:
            if o.get("symbol") != symbol:
                continue
            if o.get("status") not in ("ACCEPTED", "PENDING"):
                continue
            if o.get("side") == "BUY":
                buys.append(o)
            else:
                sells.append(o)
    return buys, sells


def determine_opening_price(
    buy_orders: list[dict], sell_orders: list[dict], prev_close: float = 0
) -> tuple[float | None, list[dict], list[dict], dict]:
    """核心算法：确定集合竞价开盘价。

    Args:
        buy_orders: 所有竞价买单列表
        sell_orders: 所有竞价卖单列表
        prev_close: 昨日收盘价（用于确定价格上下限）

    Returns:
        (opening_price, filled_buys, filled_sells, stats)
        - opening_price: 确定的开盘价，None 表示无法确定
        - filled_buys/sells: 以 (order, fill_qty, fill_price) 格式返回的成交记录
        - stats: 撮合统计信息
    """
    if not buy_orders or not sell_orders:
        return (
            None,
            [],
            [],
            {
                "matched_volume": 0,
                "buy_volume": sum(o["quantity"] for o in buy_orders),
                "sell_volume": sum(o["quantity"] for o in sell_orders),
                "candidate_prices": 0,
                "reason": "无买方或无卖方订单，无法形成开盘价",
            },
        )

    # 收集所有候选价格（委托价格 + prev_close）
    candidate_prices = set()
    for o in buy_orders + sell_orders:
        p = o.get("price", 0)
        if p > 0:
            candidate_prices.add(p)
    if prev_close > 0:
        candidate_prices.add(prev_close)

    if not candidate_prices:
        return None, [], [], {"matched_volume": 0, "reason": "无有效候选价格"}

    candidate_prices = sorted(candidate_prices)

    # 对每个候选价格计算匹配量
    best_price = None
    best_volume = 0
    best_imbalance = float("inf")
    best_cum_buy = 0
    best_cum_sell = 0

    for trial_price in candidate_prices:
        # 买方：委托价 >= trial_price 的累计量
        cum_buy = sum(
            o["quantity"] for o in buy_orders if o.get("price", 0) >= trial_price
        )
        # 卖方：委托价 <= trial_price 的累计量
        cum_sell = sum(
            o["quantity"] for o in sell_orders if o.get("price", 0) <= trial_price
        )

        matched = min(cum_buy, cum_sell)

        if matched > best_volume:
            best_volume = matched
            best_price = trial_price
            best_imbalance = abs(cum_buy - cum_sell)
            best_cum_buy = cum_buy
            best_cum_sell = cum_sell
        elif matched == best_volume and matched > 0:
            imbalance = abs(cum_buy - cum_sell)
            if imbalance < best_imbalance:
                best_imbalance = imbalance
                best_price = trial_price
                best_cum_buy = cum_buy
                best_cum_sell = cum_sell
            elif imbalance == best_imbalance and prev_close > 0:
                # 取更接近 prev_close 的价格
                if abs(trial_price - prev_close) < abs(best_price - prev_close):
                    best_price = trial_price
                    best_cum_buy = cum_buy
                    best_cum_sell = cum_sell

    if best_price is None or best_volume == 0:
        return (
            None,
            [],
            [],
            {
                "matched_volume": 0,
                "candidate_prices": len(candidate_prices),
                "reason": "无可匹配量",
            },
        )

    # 按委托价排序分发成交
    # 买方：价格从高到低优先成交
    sorted_buys = sorted(
        buy_orders, key=lambda o: (-o.get("price", 0), o.get("created_at", ""))
    )
    # 卖方：价格从低到高优先成交
    sorted_sells = sorted(
        sell_orders, key=lambda o: (o.get("price", 0), o.get("created_at", ""))
    )

    remaining_match = best_volume
    filled_buys = []
    filled_sells = []

    for bo in sorted_buys:
        if remaining_match <= 0:
            break
        if bo.get("price", 0) < best_price:
            continue
        qty = min(bo["quantity"], remaining_match)
        filled_buys.append((bo, qty, best_price))
        remaining_match -= qty

    remaining_match = best_volume
    for so in sorted_sells:
        if remaining_match <= 0:
            break
        if so.get("price", 0) > best_price:
            continue
        qty = min(so["quantity"], remaining_match)
        filled_sells.append((so, qty, best_price))
        remaining_match -= qty

    stats = {
        "matched_volume": best_volume,
        "opening_price": best_price,
        "cum_buy_volume": best_cum_buy,
        "cum_sell_volume": best_cum_sell,
        "unfilled_buy": best_cum_buy - best_volume,
        "unfilled_sell": best_cum_sell - best_volume,
        "candidate_prices": len(candidate_prices),
        "total_buy_orders": len(buy_orders),
        "total_sell_orders": len(sell_orders),
    }

    return best_price, filled_buys, filled_sells, stats


async def run_auction_match() -> list[dict]:
    """在 9:25 触发一次集合竞价撮合。每天只运行一次。"""
    global _last_auction_date, _last_auction_match_time

    now = _bj_now()
    today = now.date()

    if not TradingTimeChecker.is_trading_day():
        return []

    current_time = now.time()

    # 9:25:00 ~ 9:25:30 区间触发
    auction_end = time(9, 25)
    if not (auction_end <= current_time < time(9, 25, 30)):
        return []

    # 当日已撮合不重复
    if _last_auction_match_time and _last_auction_match_time.date() == today:
        return []

    _last_auction_match_time = now
    _last_auction_date = today

    logger.info("=== 集合竞价撮合开始 (9:25) ===")

    # 收集所有竞价订单按股票分组
    symbol_orders: dict[str, tuple[list[dict], list[dict]]] = defaultdict(
        lambda: ([], [])
    )
    for uid, orders in list(_auction_orders.items()):
        for o in orders:
            if o.get("status") not in ("ACCEPTED", "PENDING"):
                continue
            sym = o.get("symbol", "")
            buys, sells = symbol_orders[sym]
            if o.get("side") == "BUY":
                buys.append(o)
            else:
                sells.append(o)

    all_fills = []
    all_results = {}

    for symbol, (buys, sells) in symbol_orders.items():
        if not buys or not sells:
            continue

        # 获取前收盘价
        prev_close = 0
        try:
            from .live_prices import get_cached_price

            stock = get_cached_price(symbol) or {}
            prev_close = stock.get("prev_close", 0) or stock.get("last_price", 0)
        except Exception:
            logger.warning("集合竞价前收盘价获取失败", exc_info=True)

        opening_price, filled_buys, filled_sells, stats = determine_opening_price(
            buys, sells, prev_close
        )

        if opening_price is None:
            logger.info(f"集合竞价 {symbol}: 无法产生开盘价 - {stats.get('reason')}")
            # 未成交的竞价订单转入过渡期
            continue

        _opening_prices[symbol] = opening_price
        logger.info(
            f"集合竞价 {symbol}: 开盘价={opening_price:.2f}, "
            f"匹配量={stats['matched_volume']}股, "
            f"买方{len(filled_buys)}单 卖方{len(filled_sells)}单"
        )

        # 处理成交（支持部分成交：累计 filled_qty，未满量单保留待转入连续竞价）
        fill_events = []

        for order, qty, price in filled_buys + filled_sells:
            order["filled_qty"] = order.get("filled_qty", 0) + qty
            order["filled_amount"] = round(
                order.get("filled_amount", 0) + qty * price, 2
            )
            order["avg_fill_price"] = round(
                order["filled_amount"] / order["filled_qty"], 3
            )
            order["fill_price"] = price
            if order["filled_qty"] >= order.get("quantity", 0):
                order["status"] = "FILLED"
                order["fill_time"] = now.isoformat()
            else:
                # 部分成交：剩余未成交部分在 9:30 转入连续竞价继续撮合
                order["status"] = "PARTIALLY_FILLED"
            order["updated_at"] = now.isoformat()
            order["auction_matched"] = True

            fill_events.append(
                {
                    "order_id": order["order_id"],
                    "symbol": symbol,
                    "name": order.get("name", ""),
                    "side": order.get("side"),
                    "quantity": qty,
                    "price": price,
                    "amount": round(qty * price, 2),
                    "user_id": order.get("user_id"),
                    "filled_at": order["updated_at"],
                    "order_status": order["status"],
                    "order_type": order.get("order_type", "LIMIT"),
                    "lock_price": order.get("lock_price", price),
                    "source": "auction",
                }
            )

        all_fills.extend(fill_events)
        all_results[symbol] = {
            "opening_price": opening_price,
            "matched_volume": stats["matched_volume"],
            "fill_count": len(fill_events),
        }

        # 清理竞价簿中已全部成交的订单；未成交/部分成交的保留，待 9:30 转入连续竞价
        for uid in list(_auction_orders.keys()):
            _auction_orders[uid] = [
                o
                for o in _auction_orders[uid]
                if o.get("status") in ("ACCEPTED", "PENDING", "PARTIALLY_FILLED")
            ]

    logger.info(f"集合竞价撮合完成: {len(all_results)} 只股票, {len(all_fills)} 笔成交")
    return all_fills


def get_opening_price(symbol: str) -> float | None:
    """获取某股票今日开盘价（竞价产生）"""
    return _opening_prices.get(symbol)


def get_all_opening_prices() -> dict[str, float]:
    """获取所有已确定的开盘价"""
    return dict(_opening_prices)


def transition_to_continuous():
    """9:30 过渡期结束：将竞价未成交/部分成交订单转为连续竞价订单。

    调用时机：>= 9:30 后的首个生命周期轮询（每天只迁移一次）。
    - 竞价未成交订单自动转为连续竞价有效订单
    - 清空竞价簿
    """
    global _last_transition_date

    now = _bj_now()
    today = now.date()
    if not TradingTimeChecker.is_trading_day():
        return 0
    if now.time() < time(9, 30):
        return 0
    if _last_transition_date == today:
        return 0

    _last_transition_date = today
    logger.info("过渡期结束，竞价未成交订单转入连续竞价")

    from .order_engine import _orders, _symbol_orders

    migrated = 0
    for uid, orders in list(_auction_orders.items()):
        for o in orders:
            if o.get("status") not in ("ACCEPTED", "PENDING", "PARTIALLY_FILLED"):
                continue
            if o.get("quantity", 0) <= o.get("filled_qty", 0):
                continue  # 已全部成交，无需迁移
            oid = o.get("order_id", "")
            # 转为连续竞价有效订单：清除竞价标记，主订单簿继续撮合
            o["auction"] = False
            o["auction_mode"] = False
            if o["status"] == "PENDING":
                o["status"] = "ACCEPTED"
            o["updated_at"] = now.isoformat()
            if oid:
                _orders[oid] = o
                _symbol_orders[o.get("symbol", "")].add(oid)
                migrated += 1

    # 清空竞价簿
    _auction_orders.clear()
    if migrated:
        logger.info(f"竞价→连续竞价迁移: {migrated} 笔订单")
    return migrated


def auction_order_book_summary(symbol: str | None = None) -> dict:
    """竞价订单簿快照（供 Agent 和前端查看）"""
    if symbol:
        buys, sells = get_auction_orders_for_symbol(symbol)
    else:
        buys, sells = [], []
        for orders in _auction_orders.values():
            for o in orders:
                if o.get("status") not in ("ACCEPTED", "PENDING"):
                    continue
                (buys if o.get("side") == "BUY" else sells).append(o)

    # 按价格排序
    buys_sorted = sorted(buys, key=lambda o: -o.get("price", 0))
    sells_sorted = sorted(sells, key=lambda o: o.get("price", 0))

    buy_volume = sum(o.get("quantity", 0) for o in buys_sorted)
    sell_volume = sum(o.get("quantity", 0) for o in sells_sorted)

    return {
        "buy_orders": buys_sorted,
        "sell_orders": sells_sorted,
        "buy_volume": buy_volume,
        "sell_volume": sell_volume,
        "total_orders": len(buys_sorted) + len(sells_sorted),
    }
