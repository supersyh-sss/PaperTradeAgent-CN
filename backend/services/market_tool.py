"""行情/搜索工具：为所有 Agent 提供统一的实时数据与搜索入口

设计目标：
- 个股/指数/自选股/大盘行情一站式获取
- 通过关键词判断用户想问的是「市场整体」还是「具体股票」
- 所有 Agent 节点均可调用，避免重复实现
"""
import logging
import re

from .data_source_manager import data_source_manager

logger = logging.getLogger(__name__)
from . import db
from .indices import get_cached_indices, get_market_sentiment
from .symbol import normalize_symbol, pure_code
from .trading_time import TradingTimeChecker

# 市场/大盘类关键词
_MARKET_KEYWORDS = {
    "大盘", "市场", "行情", "指数", "走势", "涨跌", "情绪", "沪市", "深市",
    "a股", "股市", "上证", "深证", "创业板", "科创", "沪深300", "整体",
    "板块", "行业", "概念", "题材",
}

# 板块/行业类关键词（可扩展）
_SECTOR_KEYWORDS = {
    "银行", "证券", "保险", "白酒", "新能源", "医药", "半导体", "科技",
}


def _looks_like_market_query(query: str) -> bool:
    """判断查询是否是市场整体类问题"""
    q = query.lower()
    for kw in _MARKET_KEYWORDS:
        if kw in q:
            return True
    return False


async def get_stock_realtime(symbol: str) -> dict | None:
    """获取单只股票实时行情"""
    code = pure_code(symbol)
    if not code:
        return None
    data = await data_source_manager.get_realtime([code])
    return data.get(code)


async def get_index_overview() -> dict:
    """获取大盘指数与市场情绪概览"""
    indices = get_cached_indices()
    sentiment = get_market_sentiment()
    status = TradingTimeChecker.trading_status_info()

    up_count = sum(1 for d in indices.values() if d.get("change_pct", 0) > 0)
    down_count = sum(1 for d in indices.values() if d.get("change_pct", 0) < 0)
    flat_count = len(indices) - up_count - down_count

    leaders = sorted(
        [d for d in indices.values() if d.get("change_pct") is not None],
        key=lambda x: x.get("change_pct", 0),
        reverse=True,
    )[:3]
    laggards = sorted(
        [d for d in indices.values() if d.get("change_pct") is not None],
        key=lambda x: x.get("change_pct", 0),
    )[:3]

    return {
        "type": "market_overview",
        "status": status,
        "sentiment": sentiment,
        "summary": {
            "up_count": up_count,
            "down_count": down_count,
            "flat_count": flat_count,
            "total": len(indices),
        },
        "indices": indices,
        "leaders": leaders,
        "laggards": laggards,
    }


async def get_watchlist_snapshot(user_id: str) -> list[dict]:
    """获取用户自选股的实时行情快照"""
    watchlist = await db.get_watchlist(user_id)
    if not watchlist:
        return []
    symbols = [w["symbol"] for w in watchlist]
    realtime = await data_source_manager.get_realtime(symbols)
    result = []
    for item in watchlist:
        sym = item["symbol"]
        quote = realtime.get(sym, {})
        result.append({
            "symbol": sym,
            "name": quote.get("name") or item.get("name", sym),
            "price": quote.get("price", 0),
            "change_pct": quote.get("change_pct", 0),
            "volume": quote.get("volume", 0),
        })
    return result


async def search_market(query: str, user_id: str = "default") -> dict:
    """市场搜索入口：根据关键词返回个股行情或大盘概览

    返回结构：
    {
        "type": "market_overview" | "stock" | "unknown",
        "query": query,
        "data": dict | None,
        "reason": str,
    }
    """
    if not query:
        return {"type": "unknown", "query": query, "data": None, "reason": "空查询"}

    # 1. 优先判断市场整体类问题
    if _looks_like_market_query(query):
        return {
            "type": "market_overview",
            "query": query,
            "data": await get_index_overview(),
            "reason": "命中市场/大盘关键词",
        }

    # 2. 尝试提取股票代码或名称
    try:
        symbol = normalize_symbol(query)
        # normalize_symbol 返回带前缀代码；进一步拿名称
        if symbol:
            rt = await get_stock_realtime(symbol)
            if rt:
                return {
                    "type": "stock",
                    "query": query,
                    "data": rt,
                    "reason": "通过股票代码/名称匹配到实时行情",
                }
    except Exception:
        logger.debug("股票代码/名称匹配行情失败", exc_info=True)

    # 3. 尝试从自选股匹配名称
    try:
        watchlist = await db.get_watchlist(user_id)
        for item in watchlist:
            if item.get("name") in query or query in item.get("name", ""):
                rt = await get_stock_realtime(item["symbol"])
                if rt:
                    return {
                        "type": "stock",
                        "query": query,
                        "data": rt,
                        "reason": "通过自选股名称匹配到实时行情",
                    }
    except Exception:
        logger.debug("自选股名称匹配行情失败", exc_info=True)

    # 4. 兜底：如果查询包含数字且像代码，仍返回 overview
    if re.search(r"\d{6}", query):
        return {
            "type": "unknown",
            "query": query,
            "data": None,
            "reason": "疑似股票代码，但未能获取行情",
        }

    return {
        "type": "market_overview",
        "query": query,
        "data": await get_index_overview(),
        "reason": "未命中个股，返回市场概览兜底",
    }
