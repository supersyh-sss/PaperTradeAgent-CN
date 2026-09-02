"""A股交易规则校验 - T+1/手数/涨跌停/交易时间"""

from datetime import date, datetime, time, timedelta, timezone

BJT = timezone(timedelta(hours=8))

# 交易时间段
MORNING_START = time(9, 30)
MORNING_END = time(11, 30)
AFTERNOON_START = time(13, 0)
AFTERNOON_END = time(15, 0)

# 涨跌停幅度
LIMIT_MAIN = 0.10  # 主板±10%
LIMIT_CHINEXT = 0.20  # 创业板/科创板±20%
LIMIT_BJ = 0.30  # 北交所±30%


def is_trading_time() -> bool:
    """当前是否为A股交易时间"""
    now = datetime.now(BJT)
    if now.weekday() >= 5:  # 周末
        return False
    t = now.time()
    return (MORNING_START <= t <= MORNING_END) or (
        AFTERNOON_START <= t <= AFTERNOON_END
    )


def get_trading_status() -> dict:
    """获取当前交易状态详情"""
    now = datetime.now(BJT)
    if now.weekday() >= 5:
        return {
            "status": "closed",
            "is_trading": False,
            "detail": "周末休市",
            "next_trading_day": _next_trading_day(now).isoformat(),
        }
    t = now.time()
    if t < MORNING_START:
        return {
            "status": "pre_market",
            "is_trading": False,
            "detail": "盘前（等待9:30开盘）",
            "next_trading_day": now.date().isoformat(),
        }
    if MORNING_START <= t <= MORNING_END:
        return {
            "status": "trading",
            "is_trading": True,
            "detail": "交易中（早盘）",
            "next_trading_day": now.date().isoformat(),
        }
    if t < AFTERNOON_START:
        return {
            "status": "lunch_break",
            "is_trading": False,
            "detail": "午间休市",
            "next_trading_day": now.date().isoformat(),
        }
    if AFTERNOON_START <= t <= AFTERNOON_END:
        return {
            "status": "trading",
            "is_trading": True,
            "detail": "交易中（午盘）",
            "next_trading_day": now.date().isoformat(),
        }
    return {
        "status": "post_market",
        "is_trading": False,
        "detail": "已收盘",
        "next_trading_day": _next_trading_day(now).isoformat(),
    }


def _next_trading_day(now: datetime) -> date:
    """下一个交易日"""
    d = now.date() + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def get_price_limit(symbol: str) -> float:
    """获取股票涨跌停幅度"""
    code = symbol.replace("sh", "").replace("sz", "").replace("bj", "")
    if code.startswith(("30", "688")):
        return LIMIT_CHINEXT  # 创业板/科创板
    if code.startswith(("8", "4")):
        return LIMIT_BJ  # 北交所
    return LIMIT_MAIN


def validate_trade(
    symbol: str,
    side: str,
    quantity: int,
    price: float,
    prev_close: float,
    balance: float | None = None,
    position_qty: int = 0,
    position_buy_date: str | None = None,
) -> tuple[bool, str | None]:
    """
    校验交易合法性。返回 (是否合法, 错误信息)
    """
    # 1. 手数检查
    if quantity % 100 != 0:
        return False, f"股数必须为100的整数倍（一手=100股），当前{quantity}股"
    if quantity < 100:
        return False, f"最小交易单位为100股（一手），当前{quantity}股"

    # 2. 涨跌停检查
    limit = get_price_limit(symbol)
    limit_up = round(prev_close * (1 + limit), 2)
    limit_down = round(prev_close * (1 - limit), 2)
    if price > limit_up:
        return False, f"买入价{price}超过涨停价{limit_up}（+{limit * 100:.0f}%）"
    if price < limit_down:
        return False, f"卖出价{price}低于跌停价{limit_down}（-{limit * 100:.0f}%）"

    # 3. 价格偏离检查（超过5%偏离提示风险）
    deviation = abs(price - prev_close) / prev_close if prev_close > 0 else 0
    if deviation > 0.05:
        return False, f"交易价{price}偏离昨收{prev_close}超过5%，可能存在异常"

    # 4. 买入：余额检查
    if side == "BUY" and balance is not None:
        cost = round(price * quantity, 2)
        if balance < cost:
            return False, f"余额不足。需要{cost:.2f}，可用{balance:.2f}"

    # 5. 卖出：持仓检查
    if side == "SELL":
        if position_qty <= 0:
            return False, "没有该股票的持仓"
        if position_qty < quantity:
            return False, f"持仓不足。需要{quantity}股，持有{position_qty}股"

        # 6. T+1检查
        if position_buy_date:
            today = datetime.now(BJT).date().isoformat()
            if position_buy_date == today:
                return False, "T+1限制：今日买入的股票需下一交易日方可卖出"

    return True, None


def suggest_lot_size(balance: float, price: float, max_pct: float = 0.95) -> int:
    """根据余额和建议仓位比例计算建议股数"""
    max_cost = balance * max_pct
    max_shares = int(max_cost / price)
    # 取整到100的倍数
    lots = (max_shares // 100) * 100
    return max(lots, 100)


def estimate_executable_price(
    current_price: float,
    prev_close: float,
    volatility: float,
    direction: str = "BUY",
    confidence: float = 0.9,
) -> dict:
    """
    预估一分钟内高概率成交价。
    - direction: "BUY" → 略高于当前价（确保买入）；"SELL" → 略低于当前价（确保卖出）
    - volatility: 波动率（如1.5表示1.5%）
    - confidence: 置信度(0-1)，越高价格越激进
    """
    adj = volatility * 0.01 * confidence
    if direction == "BUY":
        price = round(current_price * (1 + adj * 0.3), 2)  # 买入价轻微上浮
        note = f"基于当前价{current_price}、波动率{volatility}%，预估买入价{price}，1分钟内成交概率约{int(confidence * 100)}%"
    else:
        price = round(current_price * (1 - adj * 0.3), 2)  # 卖出价轻微下浮
        note = f"基于当前价{current_price}、波动率{volatility}%，预估卖出价{price}，1分钟内成交概率约{int(confidence * 100)}%"

    # 确保不超出涨跌停
    limit = get_price_limit("")
    limit_up = round(prev_close * (1 + limit), 2)
    limit_down = round(prev_close * (1 - limit), 2)
    price = max(limit_down, min(limit_up, price))

    return {"estimated_price": price, "current_price": current_price, "note": note}
