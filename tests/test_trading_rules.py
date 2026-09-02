"""交易规则核心的确定性测试（不触网、不依赖真实时钟）。

覆盖三个业务核心模块：
- fee_calculator：佣金/印花税/过户费（大厂面试常考的“规则精度”）
- trade_rules：手数、涨跌停、T+1、余额/持仓校验
- trading_time：交易日/交易时段/集合竞价阶段的边界判断

时间相关断言通过重定向 TradingTimeChecker._now 固定时钟（UTC+8），
并把 chinese_calendar 置空以走 HOLIDAYS_FALLBACK 兜底路径，保证结果与当前日期无关。
"""

from datetime import datetime, timedelta, timezone

import pytest

from backend.services import fee_calculator, trade_rules
from backend.services.fee_calculator import calculate_fee, calculate_net_amount
from backend.services.trading_time import AuctionPhase, TradingTimeChecker

BJT = timezone(timedelta(hours=8))


def _at(y: int, mo: int, d: int, h: int, mi: int) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=BJT)


def _freeze_time(dt: datetime):
    """把 TradingTimeChecker 的“当前时刻”钉死到指定北京时间（classmethod 重写）。"""
    TradingTimeChecker._now = classmethod(lambda cls: dt)


@pytest.fixture(autouse=True)
def _reset_clock():
    yield
    # 恢复真实的 _now，避免影响其它用例
    TradingTimeChecker._now = classmethod(lambda cls: datetime.now(BJT))


# chinese_calendar 只在需要确定性的场景局部置空
@pytest.fixture()
def no_chinese_calendar(monkeypatch):
    monkeypatch.setattr("backend.services.trading_time.cc", None)


# ───────────────────────── 1. 费用计算器 ─────────────────────────


class TestFeeCalculator:
    def test_buy_sh_breakdown(self):
        fee, breakdown = calculate_fee(100_000, "BUY", "sh")
        # 佣金 100000*0.00025=25；印花税=0；过户费=1
        assert breakdown["佣金"] == 25.0
        assert breakdown["印花税"] == 0.0
        assert breakdown["过户费"] == 1.0
        assert fee == 26.0
        assert breakdown["合计"] == fee

    def test_sell_adds_stamp_tax(self):
        fee, breakdown = calculate_fee(100_000, "sell", "sh")
        assert breakdown["印花税"] == 50.0  # 0.05% 单边
        assert fee == pytest.approx(25.0 + 50.0 + 1.0)

    def test_min_commission_floor(self):
        # 金额很小 → 佣金被保底到 5 元
        fee, breakdown = calculate_fee(1_000, "BUY", "sz")
        assert breakdown["佣金"] == 5.0
        # 深市无过户费 → 总费用恰为最低佣金
        assert fee == 5.0

    def test_transfer_fee_only_sh(self):
        _, sz = calculate_fee(100_000, "BUY", "sz")
        _, sh = calculate_fee(100_000, "BUY", "sh")
        assert sz["过户费"] == 0.0
        assert sh["过户费"] == 1.0

    def test_custom_commission_rate(self):
        fee, breakdown = calculate_fee(100_000, "BUY", "sh", commission_rate=0.0001)
        assert breakdown["佣金"] == 10.0  # 万1
        assert fee == 11.0

    def test_net_amount_buy_and_sell(self):
        # 买入净额 = -(成交额+费用)
        net, fee = calculate_net_amount(10.0, 1000, "BUY", "sh")  # 1 万元
        assert net == -(10_000 + fee)
        # 卖出净额 = +(成交额-费用)，且卖出要交印花税
        net, fee = calculate_net_amount(10.0, 1000, "SELL", "sh")
        assert net == 10_000 - fee
        assert fee > 2.5 + 5.0  # 印花税50 + 佣金5 + 过户费1

    def test_format_fee_estimate_contains_key_facts(self):
        text = fee_calculator.format_fee_estimate(10.0, 1000, "BUY", "sh")
        assert "预估成交金额" in text and "费用明细" in text and "佣金" in text
        sell_text = fee_calculator.format_fee_estimate(10.0, 1000, "SELL", "sh")
        assert "印花税" in sell_text and "预估到账净额" in sell_text


# ───────────────────────── 2. 交易规则 ─────────────────────────


class TestTradeRules:
    def test_lot_size_rules(self):
        ok, err = trade_rules.validate_trade("000001", "BUY", 150, 10, 10)
        assert not ok and "100的整数倍" in err
        # 非整手的数量同样先命中“100 的整数倍”校验
        ok, err = trade_rules.validate_trade("000001", "BUY", 50, 10, 10)
        assert not ok and err is not None

    def test_price_limits_by_board(self):
        assert trade_rules.get_price_limit("600000") == 0.10  # 主板
        assert trade_rules.get_price_limit("300750") == 0.20  # 创业板
        assert trade_rules.get_price_limit("688981") == 0.20  # 科创板
        assert trade_rules.get_price_limit("bj832566") == 0.30  # 北交所
        assert trade_rules.get_price_limit("430047") == 0.30  # 北交所(4 开头)

    def test_validate_rejects_price_beyond_limit(self):
        ok, err = trade_rules.validate_trade(
            "000001", "BUY", 100, price=12.0, prev_close=10.0
        )
        assert not ok and "涨停价" in err
        ok, err = trade_rules.validate_trade(
            "000001", "SELL", 100, price=8.0, prev_close=10.0, position_qty=100
        )
        assert not ok and "跌停价" in err

    def test_validate_rejects_abnormal_deviation(self):
        ok, err = trade_rules.validate_trade(
            "000001", "BUY", 100, price=10.6, prev_close=10.0
        )
        assert not ok and "5%" in err

    def test_buy_insufficient_balance(self):
        ok, err = trade_rules.validate_trade(
            "000001", "BUY", 100, price=100.0, prev_close=100.0, balance=5000
        )
        assert not ok and "余额不足" in err

    def test_sell_position_checks(self):
        ok, err = trade_rules.validate_trade(
            "000001", "SELL", 100, price=10.0, prev_close=10.0, position_qty=0
        )
        assert not ok and "没有该股票" in err
        ok, err = trade_rules.validate_trade(
            "000001", "SELL", 300, price=10.0, prev_close=10.0, position_qty=200
        )
        assert not ok and "持仓不足" in err

    def test_t_plus_1_restriction(self):
        # 昨收价格、quantity=100 一手、余额足够 → 唯一失败点应为 T+1
        today = datetime.now(BJT).date().isoformat()
        ok, err = trade_rules.validate_trade(
            "000001",
            "SELL",
            100,
            price=10.0,
            prev_close=10.0,
            position_qty=100,
            position_buy_date=today,
        )
        assert not ok and "T+1" in err
        # 昨日（或更早）买入 → 可卖
        ok, err = trade_rules.validate_trade(
            "000001",
            "SELL",
            100,
            price=10.0,
            prev_close=10.0,
            position_qty=100,
            position_buy_date="2000-01-03",
        )
        assert ok and err is None

    def test_valid_trade_passes(self):
        ok, err = trade_rules.validate_trade(
            "000001", "BUY", 100, price=10.0, prev_close=10.0, balance=10_000
        )
        assert ok is True and err is None

    def test_suggest_lot_size(self):
        assert trade_rules.suggest_lot_size(500_000, 10.0) == 47500
        assert trade_rules.suggest_lot_size(10_000, 100.0) == 100  # 最低一手

    def test_estimate_executable_price_clamped_by_limit(self):
        r = trade_rules.estimate_executable_price(
            current_price=10.0,
            prev_close=10.0,
            volatility=1.5,
            direction="BUY",
            confidence=0.9,
        )
        assert r["estimated_price"] <= round(10 * 1.1, 2)  # 不超涨停
        assert r["note"] and "预估买入价" in r["note"]

    def test_estimate_sell_side_slightly_lower(self):
        r = trade_rules.estimate_executable_price(
            current_price=10.0,
            prev_close=10.0,
            volatility=1.5,
            direction="SELL",
            confidence=0.9,
        )
        # 卖出略下浮但仍在跌停价上方
        assert r["estimated_price"] < 10.0
        assert r["estimated_price"] >= round(10 * 0.9, 2)


# ───────────────────────── 3. 交易时间判定 ─────────────────────────


class TestTradingTime:
    def test_weekday_morning_trading(self, no_chinese_calendar):
        _freeze_time(_at(2026, 9, 2, 10, 0))  # 周三
        assert TradingTimeChecker.is_trading_day()
        assert TradingTimeChecker.is_trading_time()
        assert TradingTimeChecker.get_refresh_interval() == 30

    def test_lunch_break_and_post_market(self, no_chinese_calendar):
        _freeze_time(_at(2026, 9, 2, 12, 0))
        assert not TradingTimeChecker.is_trading_time()
        assert TradingTimeChecker.is_auction_phase() is False
        _freeze_time(_at(2026, 9, 2, 15, 30))
        assert not TradingTimeChecker.is_trading_time()

    def test_weekend_closed(self, no_chinese_calendar):
        _freeze_time(_at(2026, 9, 5, 10, 0))  # 周六
        assert not TradingTimeChecker.is_trading_day()
        assert not TradingTimeChecker.is_trading_time()
        assert TradingTimeChecker.get_refresh_interval() == 300

    def test_holiday_uses_fallback(self, no_chinese_calendar):
        _freeze_time(_at(2026, 10, 1, 10, 0))  # 国庆节（fallback 集合内）
        assert not TradingTimeChecker.is_trading_day()
        info = TradingTimeChecker.trading_status_info()
        assert info["status"] == "closed"
        assert "非交易日" in info["detail"] or "休市" in info["detail"]

    def test_auction_phases_boundaries(self, no_chinese_calendar):
        # 9:15-9:20 可挂可撤
        _freeze_time(_at(2026, 9, 2, 9, 18))
        assert TradingTimeChecker.get_auction_phase() == AuctionPhase.AUCTION_ORDER
        assert TradingTimeChecker.is_auction_cancellable()
        # 9:20-9:25 锁定不可撤
        _freeze_time(_at(2026, 9, 2, 9, 22))
        assert TradingTimeChecker.get_auction_phase() == AuctionPhase.AUCTION_LOCKED
        assert not TradingTimeChecker.is_auction_cancellable()
        # 9:25-9:30 过渡期可撤
        _freeze_time(_at(2026, 9, 2, 9, 27))
        assert TradingTimeChecker.get_auction_phase() == AuctionPhase.TRANSITION
        assert TradingTimeChecker.is_auction_cancellable()
        # 9:30 后非竞价
        _freeze_time(_at(2026, 9, 2, 9, 45))
        assert TradingTimeChecker.get_auction_phase() == AuctionPhase.CLOSED

    def test_auction_active_info(self, no_chinese_calendar):
        _freeze_time(_at(2026, 9, 2, 9, 22))
        info = TradingTimeChecker.auction_active_info()
        assert info["is_auction"] and info["can_order"] and not info["can_cancel"]
        _freeze_time(_at(2026, 9, 2, 15, 0))
        closed = TradingTimeChecker.auction_active_info()
        assert not closed["is_auction"]

    def test_order_acceptable_across_auction_and_continuous(self, no_chinese_calendar):
        _freeze_time(_at(2026, 9, 2, 9, 16))  # 集合竞价可下单
        assert TradingTimeChecker.is_order_acceptable()
        _freeze_time(_at(2026, 9, 2, 14, 30))
        assert TradingTimeChecker.is_order_acceptable()
        _freeze_time(_at(2026, 9, 2, 11, 45))  # 午休不可下单
        assert not TradingTimeChecker.is_order_acceptable()

    def test_trading_status_info_variants(self, no_chinese_calendar):
        _freeze_time(_at(2026, 9, 2, 10, 0))
        info = TradingTimeChecker.trading_status_info()
        assert info["status"] == "trading" and info["is_trading"]
        assert info["next_trading_day"] == "2026-09-03"
        # market_status_text 摘要不抛错且含关键信息
        text = TradingTimeChecker.market_status_text()
        assert "交易" in text

    def test_full_time_context(self, no_chinese_calendar):
        _freeze_time(_at(2026, 9, 2, 9, 22))
        text = TradingTimeChecker.full_time_context()
        assert "北京时间" in text and "9:22" in text and "下一交易日" in text

    def test_get_next_trading_day_skips_weekend(self, no_chinese_calendar):
        # 2026-09-04 是周五 → 下一交易日为下周一 09-07
        next_day = TradingTimeChecker.get_next_trading_day(
            _at(2026, 9, 4, 15, 30).date()
        )
        assert next_day.date().isoformat() == "2026-09-07"
