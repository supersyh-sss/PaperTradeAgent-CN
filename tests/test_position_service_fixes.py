"""持仓/资金统一结算（apply_trade_fill）回归测试 — P0/P1 修复验证。

覆盖：
1. T+1：当日买入的股票立即卖出 → 明确报错，持仓/余额不被误扣（含"超额卖出不吃掉当日冻结股"）
2. 余额不足：买入金额+费用超出可用余额 → 原子回滚（不产生半笔流水/半截持仓）
3. 多片段部分成交：同一 order_id 多次成交在 DB trades 行内累计 filled_qty/filled_amount，
   未满量置 PARTIALLY_FILLED，满量才置 FILLED（不复写/不重复插行）
4. 卖出部分成交：realized_pnl 按片段累计，清仓后持仓删除、余额回款正确
5. /api/portfolio 视图：sellable_quantity / t1_quantity / locked_shares / tradable_quantity /
   locked_balance / available_balance 正确反映 T+1 冻结与挂单锁定

所有用例跑在独立临时 SQLite 上（monkeypatch db.DB_PATH），不触碰真实库。
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

BJT = timezone(timedelta(hours=8))


def _date_shift(days: int) -> str:
    """北京时间的偏移日期（Y-m-d），与 services.position_service._today_str 同源"""
    return (datetime.now(BJT) + timedelta(days=days)).date().isoformat()


# ── 夹具：每个用例独立临时 DB + 干净的内存订单簿 ─────────────────────────


@pytest.fixture(autouse=True)
def _clean_in_memory_state():
    """清理 order_engine / auction_engine 模块级内存状态，避免用例间串扰。"""
    from backend.services import auction_engine, order_engine

    order_engine._orders.clear()
    order_engine._symbol_orders.clear()
    order_engine._locked_balances.clear()
    order_engine._locked_positions.clear()
    order_engine._last_auto_cancel_date = None
    auction_engine._auction_orders.clear()
    auction_engine._last_auction_date = None
    auction_engine._last_auction_match_time = None
    auction_engine._last_transition_date = None
    yield
    order_engine._orders.clear()
    order_engine._symbol_orders.clear()
    order_engine._locked_balances.clear()
    order_engine._locked_positions.clear()


@pytest.fixture()
async def db(tmp_path, monkeypatch):
    """把 db.DB_PATH 指向独立临时库并执行建表；账户余额统一归一化为 100 万，
    避免受 .env 的 INITIAL_BALANCE(500 万) 影响，保证断言确定性。"""
    from backend.services import db as db_mod

    target = tmp_path / "test_paper_trade.db"
    monkeypatch.setattr(db_mod, "DB_PATH", str(target))
    await db_mod.init_db()
    async with db_mod.transaction() as conn:
        await conn.execute(
            "UPDATE accounts SET balance = 1000000, total_assets = 1000000 "
            "WHERE user_id = 'default'"
        )
    return db_mod


# ── 1. T+1：当日买入不可卖出 ──────────────────────────────────────────────


async def test_sell_today_buy_rejected_t1(db):
    """当日买入 100 股后立即卖出 → 报错且持仓/余额不变。"""
    from backend.services import position_service as ps

    r = await ps.apply_trade_fill(
        "default",
        "000001",
        "平安银行",
        "BUY",
        100,
        10.00,
        1000.00,
    )
    assert r["success"] is True

    # 卖出当日买入的 100 股（全部处于 T+1 冻结）
    with pytest.raises(ValueError, match="可卖数量不足"):
        await ps.apply_trade_fill(
            "default",
            "000001",
            "平安银行",
            "SELL",
            100,
            10.00,
            1000.00,
        )

    acct = await db.get_account("default")
    assert float(acct["balance"]) == pytest.approx(999_000.00)
    pos = await db.get_position("default", "000001")
    assert pos and pos["quantity"] == 100 and pos["t1_quantity"] == 100
    # 只应存在买入这一笔流水
    orders = await db.get_all_orders_db("default")
    assert len(orders) == 1 and orders[0]["side"] == "BUY"


async def test_sell_crossing_frozen_portion_keeps_frozen_shares(db):
    """持有 300 股（昨日 200 可卖 + 今日买入 100 冻结），试图卖 350 股 → 明确报错。
    验证"超额卖出不会吃掉当日冻结股"：持仓数量与冻结数量均保持不变。"""
    from backend.services import db as db_mod
    from backend.services import position_service as ps

    # 昨日已持有 300 股（T+1 已解锁）
    await db_mod.upsert_position(
        "default",
        "000002",
        "深发展A",
        300,
        10.00,
        3000.00,
        _date_shift(-1),
        latest_price=10.00,
        t1_quantity=0,
        t1_date=_date_shift(-1),
    )
    # 今日再买 100 股 → 400 股中 100 股今日冻结，300 股可卖
    await ps.apply_trade_fill(
        "default",
        "000002",
        "深发展A",
        "BUY",
        100,
        10.00,
        1000.00,
    )

    # 卖出 350 > 可卖 300 → 必须失败，且不得部分成交掉冻结的 100 股
    with pytest.raises(ValueError, match="可卖数量不足"):
        await ps.apply_trade_fill(
            "default",
            "000002",
            "深发展A",
            "SELL",
            350,
            11.00,
            3850.00,
        )

    acct = await db.get_account("default")
    assert float(acct["balance"]) == pytest.approx(999_000.00)
    pos = await db.get_position("default", "000002")
    assert pos["quantity"] == 400
    assert pos["t1_quantity"] == 100


# ── 2. 买入余额不足 → 原子回滚 ────────────────────────────────────────────


async def test_buy_overdraw_rejected_atomically(db):
    """买入金额+费用超过余额 → 余额不足报错；无持仓、无流水残留（整体回滚）。"""
    from backend.services import position_service as ps

    with pytest.raises(ValueError, match="余额不足"):
        await ps.apply_trade_fill(
            "default",
            "000003",
            "浦发银行",
            "BUY",
            100,
            30_000.00,
            3_000_000.00,
        )

    acct = await db.get_account("default")
    assert float(acct["balance"]) == pytest.approx(1_000_000.00)
    assert float(acct["total_assets"]) == pytest.approx(1_000_000.00)
    assert await db.get_position("default", "000003") is None
    assert await db.get_all_orders_db("default") == []


# ── 3. 买入多片段部分成交 → DB 行内累计，状态机正确 ──────────────────────


async def test_buy_partial_fills_accumulate_on_order_row(db):
    """同一委托(oid, 1000股)分 3 片段成交：
    300@10 → 500@10.5 → 500@10，filled_qty/filled_amount 单调累计，
    前两段 PARTIALLY_FILLED，末段到量才 FILLED；不产生重复流水行。"""
    from backend.services import db as db_mod
    from backend.services import position_service as ps

    oid = "ord-partial-buy-001"
    # 前置委托行（由 api/trade.py 在下单时创建）
    await db_mod.insert_trade(
        "default",
        "000001",
        "平安银行",
        "BUY",
        "LIMIT",
        1000,
        10.00,
        10_000.00,
        status="ACCEPTED",
        order_id=oid,
    )

    await ps.apply_trade_fill(
        "default",
        "000001",
        "平安银行",
        "BUY",
        300,
        10.00,
        3000.00,
        order_id=oid,
        order_type="LIMIT",
    )
    row = await _find_order(db, oid)
    assert row["status"] == "PARTIALLY_FILLED"
    assert row["filled_qty"] == 300
    assert float(row["filled_amount"]) == pytest.approx(3000.00)

    await ps.apply_trade_fill(
        "default",
        "000001",
        "平安银行",
        "BUY",
        200,
        10.50,
        2100.00,
        order_id=oid,
        order_type="LIMIT",
    )
    row = await _find_order(db, oid)
    assert row["status"] == "PARTIALLY_FILLED"
    assert row["filled_qty"] == 500
    assert float(row["filled_amount"]) == pytest.approx(5100.00)
    assert float(row["fill_price"]) == pytest.approx(10.200)

    await ps.apply_trade_fill(
        "default",
        "000001",
        "平安银行",
        "BUY",
        500,
        10.00,
        5000.00,
        order_id=oid,
        order_type="LIMIT",
    )
    row = await _find_order(db, oid)
    assert row["status"] == "FILLED"
    assert row["filled_qty"] == 1000
    assert float(row["filled_amount"]) == pytest.approx(10_100.00)
    assert float(row["fill_price"]) == pytest.approx(10.100)

    # 全量成交后：持仓 1000 股、均价含各片段、余额扣款 = 各片段之和
    pos = await db.get_position("default", "000001")
    assert pos["quantity"] == 1000
    assert float(pos["total_cost"]) == pytest.approx(10_100.00)
    assert float(pos["avg_cost"]) == pytest.approx(10.100)
    acct = await db.get_account("default")
    assert float(acct["balance"]) == pytest.approx(989_900.00)

    # 流水行不重复：order_id 唯一，累计只落一行
    orders = await db.get_all_orders_db("default")
    assert len([o for o in orders if o["order_id"] == oid]) == 1


# ── 4. 卖出多片段部分成交 → realized_pnl 累计、清仓删除 ───────────────────


async def test_sell_partial_fills_accumulate_pnl(db):
    """昨建仓 600 股@10（T+1 已解锁），委托卖 600 股分两段：
    200@11 → 400@12；realized_pnl 按 (卖价-均价)*量 逐段累计到 1000；
    末段清仓后持仓删除、余额回款正确。"""
    from backend.services import db as db_mod
    from backend.services import position_service as ps

    await db_mod.upsert_position(
        "default",
        "000004",
        "上汽集团",
        600,
        10.00,
        6000.00,
        _date_shift(-1),
        latest_price=10.00,
        t1_quantity=0,
        t1_date=_date_shift(-1),
    )
    oid = "ord-partial-sell-002"
    await db_mod.insert_trade(
        "default",
        "000004",
        "上汽集团",
        "SELL",
        "LIMIT",
        600,
        11.00,
        6600.00,
        status="ACCEPTED",
        order_id=oid,
    )

    await ps.apply_trade_fill(
        "default",
        "000004",
        "上汽集团",
        "SELL",
        200,
        11.00,
        2200.00,
        order_id=oid,
        order_type="LIMIT",
    )
    row = await _find_order(db, oid)
    assert row["status"] == "PARTIALLY_FILLED"
    assert float(row["realized_pnl"]) == pytest.approx(200.00)
    pos = await db.get_position("default", "000004")
    assert pos["quantity"] == 400

    await ps.apply_trade_fill(
        "default",
        "000004",
        "上汽集团",
        "SELL",
        400,
        12.00,
        4800.00,
        order_id=oid,
        order_type="LIMIT",
    )
    row = await _find_order(db, oid)
    assert row["status"] == "FILLED"
    assert row["filled_qty"] == 600
    assert float(row["realized_pnl"]) == pytest.approx(1000.00)

    assert await db.get_position("default", "000004") is None
    acct = await db.get_account("default")
    assert float(acct["balance"]) == pytest.approx(1_007_000.00)
    assert float(acct["total_assets"]) == pytest.approx(1_007_000.00)


# ── 5. /api/portfolio 视图：冻结 / 挂单锁定口径 ───────────────────────────


async def test_portfolio_locked_and_tradable_views(db, monkeypatch):
    """持仓视图：
    - 今日买入 100 股（T+1 冻结）→ sellable=0、tradable=0、t1_restricted=True
    - 昨日 200 股且另有 100 股卖出挂单锁定 → sellable=200、locked=100、tradable=100
    - 顶层 available_balance = balance - locked_balance"""
    from backend.api import portfolio as portfolio_api
    from backend.services import db as db_mod
    from backend.services import live_prices, order_engine
    from backend.services import position_service as ps

    # ① 今日买入 100 股 → T+1 冻结
    await ps.apply_trade_fill(
        "default",
        "000001",
        "平安银行",
        "BUY",
        100,
        10.00,
        1000.00,
    )
    # ② 昨日 200 股可卖（T+1 已解锁）
    await db_mod.upsert_position(
        "default",
        "000002",
        "万科A",
        200,
        10.00,
        2000.00,
        _date_shift(-1),
        latest_price=10.00,
        t1_quantity=0,
        t1_date=_date_shift(-1),
    )
    # ③ 模拟 100 股卖出挂单锁定 + 5000 元买入资金锁定
    order_engine.lock_shares("default", "000002", 100)
    order_engine.lock_funds("default", 5000.00)

    # 行情全走本地 stub，杜绝测试联网
    class _FakeDS:
        async def get_realtime(self, codes):
            prices = {
                "000001": {"price": 10.5, "prev_close": 10.0, "name": "平安银行"},
                "000002": {"price": 11.0, "prev_close": 10.0, "name": "万科A"},
            }
            return {c: prices[c] for c in codes if c in prices}

    monkeypatch.setattr(live_prices, "get_cached_price", lambda code: None)
    monkeypatch.setattr(portfolio_api, "data_source_manager", _FakeDS())

    data = await portfolio_api.get_portfolio("default")
    assert data["locked_balance"] == pytest.approx(5000.00)
    assert data["available_balance"] == pytest.approx(994_000.00)

    by_symbol = {p["symbol"]: p for p in data["positions"]}
    a, b = by_symbol["000001"], by_symbol["000002"]

    # 今日买入：100 股全部 T+1 冻结，可卖/可交易均为 0
    assert a["quantity"] == 100
    assert a["t1_quantity"] == 100
    assert a["sellable_quantity"] == 0
    assert a["tradable_quantity"] == 0
    assert a["locked_shares"] == 0
    assert a["t1_restricted"] is True

    # 昨日持仓：可卖 200，其中 100 被卖出挂单锁定 → 可交易仅 100
    assert b["quantity"] == 200
    assert b["t1_quantity"] == 0
    assert b["sellable_quantity"] == 200
    assert b["locked_shares"] == 100
    assert b["tradable_quantity"] == 100
    assert b["t1_restricted"] is False


async def _find_order(db, order_id: str) -> dict:
    """按 order_id 查流水行（唯一索引，理论最多 1 行）"""
    orders = [
        o for o in await db.get_all_orders_db("default") if o["order_id"] == order_id
    ]
    assert orders, f"未找到订单流水: {order_id}"
    return orders[0]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
