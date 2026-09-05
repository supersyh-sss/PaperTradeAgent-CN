"""Agent 工具集 — LangChain @tool 结构化函数调用

使用方式:
  from .tools import AGENT_TOOLS
  llm_with_tools = model.bind_tools(AGENT_TOOLS)

每个工具独立可用，LLM 自主决定调用时机与参数。
"""

from langchain_core.tools import tool

from ..services.data_source_manager import data_source_manager
from ..services.db import (
    get_account,
    get_active_orders_db,
    get_all_orders_db,
    get_all_positions,
    get_watchlist,
)
from ..services.indices import TRACKED_INDICES
from ..services.market_tool import get_index_overview
from ..services.news_service import get_stock_news, search_news
from ..services.symbol import pure_code
from ..services.technical_analysis import technical_analyzer
from ..services.web_search import search_web


@tool
async def get_realtime_quote(symbol: str) -> dict:
    """获取个股实时行情报价。

    Args:
        symbol: 6 位纯数字股票代码，如 "600519" 或 "000001"（不含 sh/sz 前缀）

    Returns:
        包含 price、change_pct、volume、high、low、open、prev_close 等字段的行情数据
    """
    code = pure_code(symbol.strip())
    quotes = await data_source_manager.get_realtime([code])
    if not quotes:
        return {"error": f"无法获取 {symbol} 的实时行情"}
    result = next(iter(quotes.values()))
    return {
        "symbol": code,
        "name": result.get("name", ""),
        "price": result.get("price"),
        "change_pct": result.get("change_pct"),
        "volume": result.get("volume"),
        "high": result.get("high"),
        "low": result.get("low"),
        "open": result.get("open"),
        "prev_close": result.get("prev_close"),
    }


@tool
async def get_kline_data(symbol: str, period: str = "day", count: int = 60) -> dict:
    """获取个股历史 K 线数据与技术指标。

    Args:
        symbol: 6 位纯数字股票代码
        period: 周期类型 — "day"/"week"/"month"
        count: 返回数据条数，默认 60

    Returns:
        包含 kline 数据和技术指标（MA、RSI、MACD、Bollinger）
    """
    code = pure_code(symbol.strip())
    kline_data = await data_source_manager.get_kline(code, period, count)
    if not kline_data:
        return {"error": f"无法获取 {symbol} 的K线数据"}

    indicators = technical_analyzer.analyze(kline_data)
    return {
        "symbol": code,
        "period": period,
        "data_count": len(kline_data) if isinstance(kline_data, list) else 0,
        "latest_price": kline_data[-1].get("close")
        if isinstance(kline_data, list) and kline_data
        else None,
        "indicators": indicators,
    }


@tool
async def get_stock_news_tool(symbol: str) -> dict:
    """获取个股最新公告与新闻。

    Args:
        symbol: 6 位纯数字股票代码

    Returns:
        新闻列表，含 title、source、time 等字段
    """
    code = pure_code(symbol.strip())
    result = await get_stock_news(code)
    news = result.get("data", []) if result else []
    return {
        "symbol": code,
        "news_count": len(news),
        "news": news[:8],
    }


@tool
async def search_news_tool(keyword: str, limit: int = 10) -> dict:
    """按关键词搜索财经新闻/资讯，适用于行业、主题、政策或个股消息面。

    Args:
        keyword: 搜索关键词，如 "人工智能"、"新能源汽车"、"贵州茅台"、"降准"
        limit: 返回条数，默认 10

    Returns:
        新闻列表，每项含 title、source、url、time、summary
    """
    result = await search_news(keyword, limit=limit)
    news = result.get("data", []) if result else []
    return {
        "keyword": keyword,
        "news_count": len(news),
        "news": news,
    }


@tool
async def web_search_tool(keyword: str, limit: int = 8) -> dict:
    """通过搜索引擎全网搜索任意关键词（不限于特定财经网站），获取更广泛的资讯。

    适用于财经站点新闻之外的主题、政策、行业、公司动态等泛资讯检索。

    Args:
        keyword: 搜索关键词，如 "央行降准"、"人工智能政策"、"某公司最新动态"
        limit: 返回条数，默认 8

    Returns:
        搜索结果列表，每项含 title、url、source、summary
    """
    items = await search_web(keyword, limit=limit)
    return {
        "keyword": keyword,
        "count": len(items),
        "results": items,
    }


@tool
async def get_market_overview() -> dict:
    """获取 A 股市场整体概览：主要指数涨跌、市场情绪、领涨领跌板块。

    Returns:
        市场概况数据，含 indices、sentiment、summary
    """
    overview = await get_index_overview()
    return {
        "status": overview.get("status", {}),
        "sentiment": overview.get("sentiment", {}),
        "indices": {
            sym: {
                "name": TRACKED_INDICES.get(sym, sym),
                "price": d.get("price"),
                "change_pct": d.get("change_pct"),
            }
            for sym, d in overview.get("indices", {}).items()
        },
        "summary": overview.get("summary", {}),
    }


@tool
async def get_user_portfolio(user_id: str) -> dict:
    """获取用户当前持仓明细。

    Args:
        user_id: 用户 ID，通常是 "default"

    Returns:
        持仓列表，每项含 symbol、name、quantity、avg_cost、current_price、pnl
    """
    positions = await get_all_positions(user_id)
    if not positions:
        return {"positions": [], "count": 0, "message": "无持仓"}

    return {
        "positions": [
            {
                "symbol": p.get("symbol"),
                "name": p.get("name"),
                "quantity": p.get("quantity"),
                "avg_cost": p.get("avg_cost"),
                "current_price": p.get("latest_price"),
                "pnl": p.get("pnl"),
                "pnl_pct": p.get("pnl_pct"),
            }
            for p in positions
        ],
        "count": len(positions),
    }


@tool
async def get_account_tool(user_id: str) -> dict:
    """获取用户账户资金状况。

    Args:
        user_id: 用户 ID

    Returns:
        账户信息：balance、available_balance（扣除在途买单锁定后的可用资金）、
        locked_balance、total_assets、frozen_amount
    """
    account = await get_account(user_id)
    if not account:
        return {"error": "账户不存在"}

    balance = float(account.get("balance", 0) or 0)
    total_assets = float(account.get("total_assets", 0) or 0)
    frozen = float(account.get("frozen_amount", 0) or 0)

    # 可用口径与交易/持仓页一致：余额扣除在途订单锁定的资金
    try:
        from ..services.order_engine import get_locked_balance

        locked = float(get_locked_balance(user_id) or 0)
    except Exception:
        locked = 0.0

    return {
        "balance": round(balance, 2),
        "available_balance": round(max(balance - locked, 0), 2),
        "locked_balance": round(locked, 2),
        "frozen_amount": round(frozen, 2),
        "total_assets": round(total_assets, 2),
    }


@tool
async def get_watchlist_tool(user_id: str) -> dict:
    """获取用户自选股列表。

    Args:
        user_id: 用户 ID

    Returns:
        自选股列表
    """
    watchlist = await get_watchlist(user_id)
    if not watchlist:
        return {"watchlist": [], "count": 0, "message": "自选列表为空"}

    return {
        "watchlist": [
            {"symbol": w.get("symbol"), "name": w.get("name")} for w in watchlist
        ],
        "count": len(watchlist),
    }


@tool
async def get_active_orders(user_id: str) -> dict:
    """获取用户当前活跃委托单。

    Args:
        user_id: 用户 ID

    Returns:
        活跃订单列表，含 order_id、symbol、side、price、quantity、status
    """
    orders = await get_active_orders_db(user_id)
    if not orders:
        return {"orders": [], "count": 0, "message": "无活跃订单"}

    return {
        "orders": [
            {
                "order_id": o.get("order_id"),
                "symbol": o.get("symbol"),
                "side": o.get("side"),
                "price": o.get("price"),
                "quantity": o.get("quantity"),
                "status": o.get("status"),
            }
            for o in orders
        ],
        "count": len(orders),
    }


@tool
async def get_order_history(user_id: str) -> dict:
    """获取用户历史订单记录（含卖出已实现收益）。

    Args:
        user_id: 用户 ID

    Returns:
        历史订单列表，含 symbol、name、side、price、quantity、status、
        realized_pnl（卖出时的已实现盈亏，买入时为 None）、created_at
    """
    orders = await get_all_orders_db(user_id, limit=31)
    if not orders:
        return {"orders": [], "count": 0, "message": "无历史订单"}

    has_more = len(orders) > 30
    if has_more:
        orders = orders[:30]

    return {
        "orders": [
            {
                "order_id": o.get("order_id") or o.get("id"),
                "symbol": o.get("symbol"),
                "name": o.get("name"),
                "side": o.get("side"),
                "price": o.get("price"),
                "quantity": o.get("quantity"),
                "status": o.get("status"),
                "realized_pnl": o.get("realized_pnl"),
                "created_at": o.get("created_at"),
            }
            for o in orders
        ],
        "count": len(orders),
        "has_more": has_more,
        "message": "已返回最近 30 条订单，如需更早记录请说明时间范围"
        if has_more
        else "",
    }


# 工具集注册表
AGENT_TOOLS = [
    get_realtime_quote,
    get_kline_data,
    get_stock_news_tool,
    search_news_tool,
    web_search_tool,
    get_market_overview,
    get_user_portfolio,
    get_account_tool,
    get_watchlist_tool,
    get_active_orders,
    get_order_history,
]

# 按 Agent 角色分类
TOOLS_BY_AGENT = {
    "quant_researcher": [get_realtime_quote, get_kline_data],
    "market_intelligence": [
        get_stock_news_tool,
        search_news_tool,
        web_search_tool,
        get_market_overview,
    ],
    "trade_executor": [
        get_realtime_quote,
        get_user_portfolio,
        get_account_tool,
        get_active_orders,
        get_order_history,
    ],
    "portfolio_monitor": [get_user_portfolio, get_account_tool, get_order_history],
    "response_generator": [
        get_realtime_quote,
        get_kline_data,
        get_market_overview,
        get_user_portfolio,
        search_news_tool,
        web_search_tool,
    ],
}
