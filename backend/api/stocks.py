"""股票数据 API - K线图、实时行情、股票搜索"""
from fastapi import APIRouter, Depends, HTTPException, Query
from ..services.tencent_api import tencent_api
from ..services.technical_analysis import technical_analyzer
from ..services.symbol import pure_code
from ..services.kline_cache import get_cached_kline, save_kline_to_cache
from ..middleware.error_handler import get_current_user

router = APIRouter(prefix="/api/stocks", tags=["stocks"])


@router.get("/search")
async def search_stocks(
    q: str = Query(..., min_length=1, description="搜索关键词（代码或名称）"),
    limit: int = Query(default=8, ge=1, le=20),
):
    """根据关键词搜索A股股票（基于本地全量股票列表，无需API请求）"""
    from ..services.stock_lookup import search, get_count, get_date
    results = search(q, limit)
    return {
        "query": q,
        "results": results,
        "total_db": get_count(),
        "db_date": get_date(),
    }


@router.get("/{symbol}/kline")
async def get_kline(
    symbol: str,
    days: int = Query(default=360, ge=30, le=720),
    user_id: str = Depends(get_current_user),
):
    """获取历史K线数据（优先从文件缓存读取，用于前端图表）"""
    symbol = pure_code(symbol)

    # 1. 先尝试文件缓存
    kline = get_cached_kline(symbol, "day", days)
    source = "file_cache"

    # 2. 缓存未命中或数据不足，从API获取
    if not kline or len(kline) < days:
        kline = await tencent_api.get_kline(symbol, period="day", count=days)
        if kline:
            save_kline_to_cache(symbol, "day", kline)
            source = "api"
        else:
            # API也失败，返回部分缓存
            if kline:
                source = "partial_cache"
            else:
                raise HTTPException(404, f"无法获取 {symbol} 的K线数据")

    # Calculate MAs
    closes = [k["close"] for k in kline]

    def calc_ma(values, period):
        if len(values) < period:
            return [None] * len(values)
        result = [None] * (period - 1)
        for i in range(period - 1, len(values)):
            avg = sum(values[i - period + 1:i + 1]) / period
            result.append(round(avg, 2))
        return result

    ma5 = calc_ma(closes, 5)
    ma10 = calc_ma(closes, 10)
    ma20 = calc_ma(closes, 20)
    ma60 = calc_ma(closes, 60)

    # Format for ECharts candlestick: [open, close, low, high]
    chart_data = []
    volumes = []
    dates = []
    for i, k in enumerate(kline):
        chart_data.append([k["open"], k["close"], k["low"], k["high"]])
        volumes.append(k["volume"])
        dates.append(k["date"])

    # Calculate latest technical indicators
    try:
        analysis = technical_analyzer.analyze(kline)
    except Exception:
        analysis = {}

    return {
        "symbol": symbol,
        "dates": dates,
        "data": chart_data,
        "volumes": volumes,
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "ma60": ma60,
        "latest_price": kline[-1]["close"] if kline else 0,
        "source": source,
        "indicators": {
            "rsi": analysis.get("rsi"),
            "macd_signal": analysis.get("macd_signal"),
            "trend": analysis.get("trend"),
            "volatility": analysis.get("volatility"),
            "support": analysis.get("support"),
            "resistance": analysis.get("resistance"),
            "ma": analysis.get("ma", {}),
            "bollinger": analysis.get("bollinger", {}),
        }
    }


@router.get("/{symbol}/realtime")
async def get_realtime(symbol: str, user_id: str = Depends(get_current_user)):
    """获取股票实时行情（优先使用后台轮询缓存，缓存未命中才请求腾讯）"""
    symbol = pure_code(symbol)

    from ..services.live_prices import get_cached_price, add_hot_symbol
    add_hot_symbol(symbol)
    cached = get_cached_price(symbol)
    if cached and cached.get("last_price"):
        return {
            "symbol": symbol,
            "name": cached.get("name"),
            "price": cached.get("last_price"),
            "open": cached.get("open"),
            "high": cached.get("high"),
            "low": cached.get("low"),
            "prev_close": cached.get("prev_close"),
            "volume": cached.get("volume"),
            "amount": cached.get("amount"),
            "turnover": cached.get("turnover"),
            "pe": cached.get("pe"),
            "pb": cached.get("pb"),
        }

    data = await tencent_api.get_realtime([symbol])
    if not data:
        raise HTTPException(404, f"无法获取 {symbol} 的实时行情")

    stock = data.get(symbol)
    if not stock:
        raise HTTPException(404, f"无法获取 {symbol} 的实时行情")

    return {
        "symbol": symbol,
        "name": stock.get("name"),
        "price": stock.get("price"),
        "open": stock.get("open"),
        "high": stock.get("high"),
        "low": stock.get("low"),
        "prev_close": stock.get("prev_close"),
        "volume": stock.get("volume"),
        "amount": stock.get("amount"),
        "turnover": stock.get("turnover"),
        "pe": stock.get("pe"),
        "pb": stock.get("pb"),
    }
