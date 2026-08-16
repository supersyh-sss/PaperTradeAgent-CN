"""市场行情 API - 实时行情SSE + K线数据"""
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from ..middleware.error_handler import get_current_user
from ..services.data_source_manager import data_source_manager
from ..services.kline_cache import get_cached_kline, save_kline_to_cache
from ..services.live_prices import subscribe_price_stream, get_all_cached_prices, get_cached_price, get_cached_predictions
from ..services.indices import get_cached_indices, get_market_sentiment
from ..services.symbol import pure_code

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/realtime")
async def realtime(symbols: str = Query(...), user_id: str = Depends(get_current_user)):
    """获取指定股票的实时行情"""
    codes = [pure_code(s.strip()) for s in symbols.split(",") if s.strip()]
    codes = [c for c in codes if c]
    if not codes:
        return {"data": {}}
    results = await data_source_manager.get_realtime(codes)
    return {"data": results}


@router.get("/live-prices")
async def live_prices(user_id: str = Depends(get_current_user)):
    """获取所有已缓存的实时行情（自选股）"""
    return {"data": get_all_cached_prices()}


@router.get("/price-stream")
async def price_stream(symbols: str = Query(...), user_id: str = Depends(get_current_user)):
    """SSE实时股价流：交易确认面板使用"""
    codes = [pure_code(s.strip()) for s in symbols.split(",") if s.strip()]
    codes = [c for c in codes if c]

    async def event_gen():
        async for event in subscribe_price_stream(codes):
            yield event

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@router.get("/kline/{symbol}")
async def kline(symbol: str, period: str = "day", days: int = 360,
                user_id: str = Depends(get_current_user)):
    """获取K线数据（优先从文件缓存读取）"""
    symbol = pure_code(symbol)
    # 1. 尝试文件缓存
    cached = get_cached_kline(symbol, period, days)
    if cached and len(cached) >= days:
        return {"data": cached, "source": "file_cache"}

    # 2. 缓存未命中，从API获取
    data = await data_source_manager.get_kline(symbol, period, days)
    if data:
        save_kline_to_cache(symbol, period, data)
        return {"data": data, "source": "api"}

    # 3. API也失败，返回部分缓存
    if cached:
        return {"data": cached, "source": "partial_cache"}
    return {"data": [], "source": "none"}


@router.get("/indices")
async def indices(user_id: str = Depends(get_current_user)):
    """获取大盘指数实时行情"""
    return {"data": get_cached_indices()}


@router.get("/sentiment")
async def sentiment(user_id: str = Depends(get_current_user)):
    """获取市场情绪分析"""
    return {"data": get_market_sentiment()}


@router.get("/predictions")
async def predictions(symbol: str = Query(None), user_id: str = Depends(get_current_user)):
    """获取算法预测信号"""
    all_preds = get_cached_predictions()
    if symbol:
        return {"data": all_preds.get(symbol, {})}
    return {"data": all_preds}
