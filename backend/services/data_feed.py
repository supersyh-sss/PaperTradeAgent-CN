"""Agent 共享数据订阅（Data Feed）

架构原则：
  - 交易时段：Agent 被动订阅数据流（push），由后台 live_prices 轮询器统一推送
  - 非交易时段：Agent 可通过工具主动获取（pull），避免冗余轮询
  - 持仓/账户：从 DB 获取并缓存，提供一致的快照视图
  - 历史数据：优先使用 kline_cache JSON 文件，命中后不再调 API

使用方法：
  from .data_feed import DataFeed
  feed = DataFeed()
  snapshot = await feed.get_snapshot(user_id, symbols=["sh600519"])
  # 或订阅实时推送：
  async for update in feed.subscribe(user_id, symbols=["sh600519"]):
      ...
"""

import asyncio
import logging
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta, timezone

from . import db as db_service
from .trading_time import TradingTimeChecker

logger = logging.getLogger(__name__)

BJT = timezone(timedelta(hours=8))


class DataSnapshot:
    """一次数据快照 — 所有 Agent 共享的统一视图"""

    __slots__ = (
        "account",
        "active_orders",
        "data_sources",  # 标注各字段来源（db/cache/live/api）
        "indices",
        "is_trading",
        "live_prices",
        "positions",
        "timestamp",
        "watchlist",
    )

    def __init__(self):
        self.timestamp: str = ""
        self.account: dict | None = None
        self.positions: list[dict] = []
        self.active_orders: list[dict] = []
        self.watchlist: list[dict] = []
        self.live_prices: dict[str, dict] = {}
        self.indices: dict[str, dict] = {}
        self.is_trading: bool = False
        self.data_sources: dict[str, str] = {}

    def to_context_text(self) -> str:
        """生成注入 LLM 的上下文文本"""
        parts = [f"[数据快照 {self.timestamp}]"]

        if self.account:
            avail = self.account.get("balance", 0)
            total = self.account.get("total_assets", 0)
            parts.append(
                f"账户: 可用{avail:.2f}, 总资产{total:.2f} [来源:{self.data_sources.get('account', '?')}]"
            )

        if self.positions:
            lines = []
            for p in self.positions:
                sym = p.get("symbol", "")
                qty = p.get("quantity", 0)
                name = p.get("name", "")
                cost = p.get("avg_cost", 0)
                # 优先使用实时价格
                live = self.live_prices.get(sym, {})
                cur = live.get("last_price") or p.get("latest_price", 0)
                pnl = (cur - cost) * qty if cur and cost else 0
                pnl_pct = ((cur - cost) / cost * 100) if cost else 0
                lines.append(
                    f"  {name}({sym}) 持仓{qty}股 成本{cost:.2f} 现价{cur:.2f} 盈亏{pnl:+.2f}({pnl_pct:+.1f}%)"
                )
            parts.append(
                "持仓 ["
                + self.data_sources.get("positions", "?")
                + "]:\n"
                + "\n".join(lines)
            )
        else:
            parts.append("持仓: 空仓")

        if self.active_orders:
            parts.append(
                "活跃订单: "
                + " | ".join(
                    f"{'买' if o.get('side') == 'buy' else '卖'}{o.get('symbol', '')} {o.get('price', 0)}x{o.get('quantity', 0)}"
                    for o in self.active_orders[:5]
                )
            )

        if self.watchlist:
            parts.append(
                "自选: "
                + " | ".join(
                    f"{w.get('name', '')}({w.get('symbol', '')})"
                    for w in self.watchlist[:10]
                )
            )

        # Add live prices for watchlist/positions
        if self.live_prices:
            price_lines = []
            for sym, info in list(self.live_prices.items())[:10]:
                price_lines.append(
                    f"  {sym}: {info.get('last_price', '-')} (涨跌{info.get('change_pct', '-')}%)"
                )
            if price_lines:
                parts.append(
                    "实时行情 [来源:"
                    + self.data_sources.get("prices", "?")
                    + "]:\n"
                    + "\n".join(price_lines)
                )

        # Add market indices
        if self.indices:
            idx_lines = []
            for code, info in self.indices.items():
                idx_lines.append(
                    f"  {info.get('name', code)}: {info.get('last_price', '-')} ({info.get('change_pct', 0):+.2f}%)"
                )
            if idx_lines:
                parts.append("大盘指数:\n" + "\n".join(idx_lines))

        return "\n".join(parts)


class DataFeed:
    """共享数据订阅服务

    - 交易时段返回 live_prices 缓存 + DB 快照
    - 非交易时段返回 DB 快照（Agent 应通过 tools 自行获取）
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def get_snapshot(
        self,
        user_id: str,
        symbols: list[str] | None = None,
        include_prices: bool = True,
    ) -> DataSnapshot:
        """获取当前数据快照

        Args:
            user_id: 用户 ID
            symbols: 需要实时价格的股票列表（None=所有持仓+自选）
            include_prices: 是否包含实时价格（非交易时段可关闭）
        """
        snap = DataSnapshot()
        snap.timestamp = datetime.now(BJT).isoformat()
        snap.is_trading = TradingTimeChecker.is_trading_time()

        # 并行获取所有数据
        tasks = [
            ("account", db_service.get_account(user_id)),
            ("positions", db_service.get_all_positions(user_id)),
            ("orders", db_service.get_active_orders_db(user_id)),
            ("watchlist", db_service.get_watchlist(user_id)),
        ]
        results = await asyncio.gather(*[t[1] for t in tasks], return_exceptions=True)

        for i, (key, _) in enumerate(tasks):
            result = results[i]
            if isinstance(result, Exception):
                logger.warning(f"DataFeed: {key} 获取失败: {result}")
                continue
            if key == "account":
                snap.account = result
                snap.data_sources["account"] = "db"
            elif key == "positions":
                snap.positions = result or []
                snap.data_sources["positions"] = "db"
            elif key == "orders":
                snap.active_orders = result or []
                snap.data_sources["orders"] = "db"
            elif key == "watchlist":
                snap.watchlist = result or []
                snap.data_sources["watchlist"] = "db"

        # 实时价格：交易时段使用 live_prices 缓存
        # 指数数据：始终拉取（盘后仍可使用当日收盘数据）
        if include_prices:
            try:
                from .live_prices import get_all_live_prices, get_index_overview

                snap.indices = get_index_overview()

                if snap.is_trading:
                    all_prices = get_all_live_prices()

                    # 缓存键形如 sh600519 / sz000001：先建一次 纯代码→行情 映射，
                    # 将持仓/自选逐 symbol 匹配从 O(n×m) 嵌套循环降为 O(n+m) 查找
                    code_index: dict[str, dict] = {}
                    for _cache_key, price_data in all_prices.items():
                        _code = (
                            _cache_key[-6:]
                            if len(_cache_key) >= 6 and _cache_key[-6:].isdigit()
                            else ""
                        )
                        if _code:
                            code_index.setdefault(_code, price_data)

                    # 筛选取需要的 symbol
                    if symbols:
                        for sym in symbols:
                            _pd = code_index.get(sym)
                            if _pd is not None:
                                snap.live_prices[sym] = _pd
                    else:
                        # 默认：取所有持仓 + 自选股的价格
                        interested = set()
                        for p in snap.positions:
                            interested.add(p.get("symbol", ""))
                        for w in snap.watchlist:
                            interested.add(w.get("symbol", ""))
                        for sym in interested:
                            _pd = code_index.get(sym)
                            if _pd is not None:
                                snap.live_prices[sym] = _pd

                snap.data_sources["prices"] = (
                    "live_prices_cache" if snap.is_trading else "cached_indices_only"
                )
            except Exception as e:
                logger.warning(f"DataFeed: 实时价格获取失败: {e}")
                snap.data_sources["prices"] = "unavailable"
        else:
            snap.data_sources["prices"] = (
                "skipped_non_trading" if not snap.is_trading else "skipped"
            )

        return snap

    async def subscribe(
        self,
        user_id: str,
        symbols: list[str],
        interval: float = 1.0,
    ) -> AsyncGenerator[DataSnapshot, None]:
        """订阅实时数据推送（仅交易时段有效）

        Args:
            user_id: 用户 ID
            symbols: 订阅的股票列表
            interval: 推送间隔（秒）
        """
        is_trading = TradingTimeChecker.is_trading_time()

        while is_trading:
            snapshot = await self.get_snapshot(user_id, symbols=symbols)
            yield snapshot
            await asyncio.sleep(interval)
            is_trading = TradingTimeChecker.is_trading_time()

        # 非交易时段：返回最终快照后退出
        yield await self.get_snapshot(user_id, symbols=symbols, include_prices=False)


# 便捷函数


async def get_agent_context(user_id: str) -> str:
    """获取 Agent 注入上下文字符串（供 agent_chat_node 等使用）"""
    feed = DataFeed()
    snap = await feed.get_snapshot(user_id)
    return snap.to_context_text()
