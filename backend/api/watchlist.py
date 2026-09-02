"""自选股管理 API"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..config import WATCHLIST_MAX_SIZE
from ..middleware.error_handler import get_current_user
from ..services import db
from ..services.data_source_manager import data_source_manager
from ..services.symbol import normalize_symbol, pure_code

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


class ValidateStockRequest(BaseModel):
    query: str = Field(..., description="股票名称或代码")


class AddWatchlistRequest(BaseModel):
    symbol: str
    name: str


@router.get("")
async def get_user_watchlist(user_id: str = Depends(get_current_user)):
    """获取用户自选股列表（优先使用后台轮询缓存的价格，避免重复API请求）"""
    watchlist = await db.get_watchlist(user_id)

    # Use background polling cache for instant prices (no API call)
    if watchlist:
        from ..services.live_prices import get_cached_price

        for w in watchlist:
            sym = pure_code(w["symbol"])
            w["symbol"] = sym
            stock = get_cached_price(sym) or {}
            if stock and stock.get("last_price"):
                w["price"] = stock.get("last_price", 0.0)
                w["prev_close"] = stock.get("prev_close", 0.0)
                w["change_pct"] = stock.get("change_pct", 0.0)
            else:
                # Fallback: try data_source_manager (one-time API call if cache miss)
                w["price"] = 0.0
                w["prev_close"] = 0.0
                w["change_pct"] = 0.0

    # Async fill prices for any items still at 0 via data_source_manager
    zero_price_symbols = [w["symbol"] for w in watchlist if w.get("price", 0) == 0]
    if zero_price_symbols:
        try:
            prices_data = await data_source_manager.get_realtime(zero_price_symbols)
            for w in watchlist:
                if w.get("price", 0) == 0:
                    stock = prices_data.get(w["symbol"], {})
                    w["price"] = stock.get("price", 0.0)
                    w["prev_close"] = stock.get("prev_close", 0.0)
                    w["change_pct"] = (
                        round(
                            (stock.get("price", 0) - stock.get("prev_close", 1))
                            / max(stock.get("prev_close", 1), 0.01)
                            * 100,
                            2,
                        )
                        if stock.get("prev_close") and stock.get("price")
                        else 0.0
                    )
        except Exception:
            pass

    return {"watchlist": watchlist, "count": len(watchlist), "max": WATCHLIST_MAX_SIZE}


@router.post("/validate")
async def validate_stock(
    req: ValidateStockRequest, user_id: str = Depends(get_current_user)
):
    """验证股票名称/代码是否有效（通过行情接口二次校验）"""
    query = (req.query or "").strip()
    if not query:
        raise HTTPException(400, "请输入股票代码或名称")

    # 1. 归一化为 6 位数字代码；若输入是中文名则尝试本地映射
    normalized = normalize_symbol(query)
    if not normalized:
        raise HTTPException(
            400,
            f"无法识别「{query}」，请使用正确的股票代码（如 600519、000001）或常见名称（如 茅台）",
        )

    # 2. 通过行情接口二次校验（带多源故障转移）
    try:
        data = await data_source_manager.get_realtime([normalized])
        stock = data.get(normalized)
        if stock:
            name = stock.get("name", "")
            price = stock.get("price", 0)
            if name and price > 0:
                return {
                    "valid": True,
                    "symbol": normalized,
                    "name": name,
                    "price": price,
                    "message": f"验证成功：{name}（{normalized}）当前价 {price}",
                }
    except Exception as exc:
        raise HTTPException(503, f"行情服务暂不可用，请稍后重试：{exc}")

    raise HTTPException(404, f"无法验证股票「{query}」，该股票可能已退市或代码有误")


@router.post("")
async def add_to_watchlist(
    req: AddWatchlistRequest, user_id: str = Depends(get_current_user)
):
    """添加自选股"""
    return await add_watchlist_core(user_id, req.symbol, req.name)


async def add_watchlist_core(user_id: str, symbol: str, name: str) -> dict:
    """添加自选股核心逻辑（供 HTTP 端点与 Agent 确认操作复用）"""
    symbol = pure_code(symbol)
    name = name.strip()
    if not symbol or len(symbol) != 6 or not symbol.isdigit():
        raise HTTPException(400, "股票代码格式不正确")
    if not name:
        raise HTTPException(400, "股票名称不能为空")

    current = await db.get_watchlist(user_id)
    if len(current) >= WATCHLIST_MAX_SIZE:
        raise HTTPException(400, f"自选股最多{WATCHLIST_MAX_SIZE}只，请先移除其他股票")

    # 检查是否重复（按归一化后的代码）
    for item in current:
        if pure_code(item.get("symbol", "")) == symbol:
            raise HTTPException(400, f"{name}({symbol}) 已在自选股列表中")

    await db.add_watchlist(user_id, symbol, name)
    watchlist = await db.get_watchlist(user_id)
    return {
        "success": True,
        "watchlist": watchlist,
        "message": f"已添加 {name}({symbol}) 到自选股 [{len(watchlist)}/{WATCHLIST_MAX_SIZE}]",
    }


@router.delete("/{symbol}")
async def remove_from_watchlist(symbol: str, user_id: str = Depends(get_current_user)):
    """移除自选股（如有持仓则不允许移除）"""
    return await remove_watchlist_core(user_id, symbol)


async def remove_watchlist_core(user_id: str, symbol: str) -> dict:
    """移除自选股核心逻辑（供 HTTP 端点与 Agent 确认操作复用）"""
    symbol = pure_code(symbol)
    position = await db.get_position(user_id, symbol)
    if position and position.get("quantity", 0) > 0:
        raise HTTPException(400, "该股票仍有持仓，请先卖出后再移除自选股")

    await db.remove_watchlist(user_id, symbol)
    watchlist = await db.get_watchlist(user_id)
    return {"success": True, "watchlist": watchlist, "message": "已移除自选股"}
