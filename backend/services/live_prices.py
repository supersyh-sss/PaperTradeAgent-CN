"""实时行情轮询服务 - v3: 单次批量 + 高频轮询

基于腾讯财经API限流测试结果（200+代码/次，8req/s零拒绝）优化：
- 单次请求拉取全部股票（指数+自选+用户查询热符号），每次仅1次API调用
- 1秒轮询间隔，近实时更新
- 热符号自动过期清理，防止永久膨胀
- 订单撮合 + 价格预测在同一周期内执行
"""
import asyncio
import json
import logging
import traceback
from collections.abc import AsyncGenerator
from datetime import datetime

import httpx

from ..config import (
    LIVE_PRICE_POLL_INTERVAL,
)
from . import db as db_service
from .cache import api_limiter
from .indices import TRACKED_INDICES
from .order_engine import match_orders
from .symbol import to_tencent_code
from .trading_time import TradingTimeChecker

logger = logging.getLogger(__name__)

# ─── 热符号：用户会话中临时查询的股票 ───
# {symbol: expiry_timestamp}  — 60s 无活动后自动移除
_hot_symbols: dict[str, float] = {}
_HOT_SYMBOL_TTL = 60  # 秒

# 非交易/非竞价时段的低频刷新策略：默认沿用上一次收盘缓存，仅间隔较久刷新一次
_OFF_HOURS_REFRESH_SECONDS = 300   # 非交易时段实际刷新间隔（5 分钟）
_OFF_HOURS_CHECK_SECONDS = 60      # 非交易时段状态检查粒度（1 分钟，用于及时切换回实时监控）

# 行情缓存
_live_cache: dict[str, dict] = {}
_realtime_indices: dict[str, dict] = {}
_live_cache_ts: str | None = None  # 行情缓存最后更新时间（北京时间 ISO）

# SSE 事件队列
_price_event_queues: dict[str, asyncio.Queue] = {}  # 证券代码 -> 价格事件队列
_order_event_queue: asyncio.Queue = None  # 全局订单事件队列
_last_prices: dict[str, float] = {}  # 上次价格，用于检测变动


def _get_order_queue() -> asyncio.Queue:
    global _order_event_queue
    if _order_event_queue is None:
        _order_event_queue = asyncio.Queue(maxsize=256)
    return _order_event_queue


def add_hot_symbol(symbol: str):
    """注册用户查询的热符号（用户发起非自选股查询时调用）"""
    if not symbol or len(symbol) < 6:
        return
    _hot_symbols[symbol] = asyncio.get_event_loop().time() + _HOT_SYMBOL_TTL
    logger.debug(f"注册热符号: {symbol}")


def _resolve_cache(symbol: str) -> str | None:
    """将任意格式代码解析为 _live_cache 中的 key（腾讯格式: sh/sz/bj+6位）。
    例如: "000001" → "sz000001", "sh600519" → "sh600519"
    """
    if not symbol:
        return None
    # 直接命中
    if symbol in _live_cache:
        return symbol
    # 纯6位代码 → 腾讯格式
    from .symbol import to_tencent_code
    tc = to_tencent_code(symbol)
    if tc and tc in _live_cache:
        return tc
    return None


def get_live_price(symbol: str) -> float | None:
    """获取缓存的实时价格（非阻塞）"""
    key = _resolve_cache(symbol)
    if not key:
        return None
    data = _live_cache.get(key)
    if data and data.get("last_price"):
        return float(data["last_price"])
    return None


def get_cache_item(symbol: str, key_filter: str) -> float | None:
    """获取缓存中指定字段"""
    cache_key = _resolve_cache(symbol)
    if not cache_key:
        return None
    data = _live_cache.get(cache_key)
    if data:
        return data.get(key_filter)
    return None


def get_index_overview() -> dict[str, dict]:
    """获取指数快照"""
    return dict(_realtime_indices)


def get_live_cache_time() -> str | None:
    """获取行情缓存最后更新时间（供 Agent 时间感知）"""
    return _live_cache_ts


def get_all_live_prices() -> dict[str, dict]:
    """获取全部缓存价格（仅腾讯格式 key，如 sh600519 / sz000001）。
    使用者需通过 get_cached_price() 或 _resolve_cache() 按纯代码查找。
    """
    return dict(_live_cache)


def get_live_price_batch(symbols: list[str]) -> dict[str, float]:
    """批量获取价格（返回 {symbol: price}，symbol 为纯代码格式）。
    供 settings.py 等内部模块使用。
    """
    result = {}
    for sym in symbols:
        key = _resolve_cache(sym)
        if key:
            data = _live_cache.get(key)
            if data and data.get("last_price"):
                result[sym] = float(data["last_price"])
    return result


# 向后兼容 API（供 main.py / api/ 层调用）

def get_all_cached_prices() -> dict:
    """向后兼容：返回全部缓存价格（含纯代码映射）"""
    return get_all_live_prices()


def get_cached_price(symbol: str) -> dict | None:
    """向后兼容：返回单只缓存价格（完整字段）"""
    key = _resolve_cache(symbol)
    if not key:
        return None
    return _live_cache.get(key)


def get_cached_predictions() -> dict:
    """向后兼容：价格预测（当前版本不实现预测，返回空）"""
    return {}


# 订单成交/状态回调（不再由 live_prices 管理，保留空壳兼容导入）
_fill_callback = None


def set_fill_callback(cb):
    """向后兼容：设置成交回调（已迁移至 order_engine）"""
    global _fill_callback
    _fill_callback = cb


def _on_order_status_change(order: dict):
    """向后兼容：订单状态变更通知"""
    try:
        q = _get_order_queue()
        event_data = json.dumps({
            "type": "order_status",
            "data": order,
        }, ensure_ascii=False)
        q.put_nowait(event_data)
    except asyncio.QueueFull:
        pass


async def subscribe_price_stream(codes: list[str]) -> AsyncGenerator[str, None]:
    """SSE 实时股价流（向后兼容）"""
    # 为每个 code 创建事件队列
    for code in codes:
        event_queue = asyncio.Queue(maxsize=32)
        _price_event_queues[code] = event_queue
        # 注册为热符号
        add_hot_symbol(code)
    
    try:
        while True:
            for code in codes:
                queue = _price_event_queues.get(code)
                if not queue:
                    continue
                try:
                    data = queue.get_nowait()
                    yield data
                except asyncio.QueueEmpty:
                    pass
            
            # 发送当前价格作为心跳
            price_list = []
            for code in codes:
                price = get_live_price(code)
                if price is not None:
                    price_list.append({"symbol": code, "price": price})
            if price_list:
                yield f"data: {json.dumps({'type': 'price_update', 'prices': price_list}, ensure_ascii=False)}\n\n"
            
            await asyncio.sleep(LIVE_PRICE_POLL_INTERVAL)
    finally:
        for code in codes:
            _price_event_queues.pop(code, None)


async def subscribe_order_stream(user_id: str) -> AsyncGenerator[str, None]:
    """SSE 订单状态流（向后兼容）"""
    queue = _get_order_queue()
    yield f"data: {json.dumps({'type': 'connected', 'user_id': user_id}, ensure_ascii=False)}\n\n"
    
    while True:
        try:
            event = await asyncio.wait_for(queue.get(), timeout=LIVE_PRICE_POLL_INTERVAL)
            yield f"data: {event}\n\n"
        except TimeoutError:
            # 心跳
            yield f"data: {json.dumps({'type': 'heartbeat'}, ensure_ascii=False)}\n\n"


def start_live_service():
    """向后兼容：启动实时行情轮询"""
    asyncio.create_task(live_price_poller())
    logger.info("实时行情轮询服务已启动（v3 批量模式）")


async def _parse_tencent_batch(text: str) -> dict[str, dict]:
    """解析腾讯批量行情响应: v_CODE="..." 或 var hq_str_CODE=..."""
    result = {}
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        try:
            # v_sh600519="...."  (批量格式) 或 var hq_str_sh600519="...." (单只格式)
            var_part, data_part = line.split("=", 1)
            raw_code = var_part.strip().strip('"')
            # 去掉 v_ / var hq_str_ 前缀
            for prefix in ("var hq_str_", "v_"):
                if raw_code.startswith(prefix):
                    raw_code = raw_code[len(prefix):]
                    break
            raw_data = data_part.strip().strip('";')
            
            if not raw_data:
                continue
            
            # 映射简化代码到完整代码
            code = _normalize_code(raw_code)
            fields = raw_data.split("~")
            if len(fields) < 32:
                continue
            
            def _f(i, _fields=fields):
                return float(_fields[i]) if i < len(_fields) and _fields[i] and _fields[i] not in ("0.000", "0") else 0.0
            
            item = {
                "name": fields[1],
                "code": fields[2],
                "last_price": _f(3),
                "prev_close": _f(4),
                "open": _f(5),
                "volume": int(fields[6]) if fields[6] else 0,
                "high": _f(33),
                "low": _f(34),
                "amount": _f(37),       # 成交额（万）
                "turnover": _f(38),     # 换手率
                "pe": _f(39),           # 市盈率
                "pb": _f(46),           # 市净率
                "change": 0.0,
                "change_pct": 0.0,
            }
            
            # 计算涨跌幅
            prev = item["prev_close"]
            if prev > 0 and item["last_price"] > 0:
                item["change"] = round(item["last_price"] - prev, 3)
                item["change_pct"] = round(item["change"] / prev * 100, 2)
            
            result[code] = item
            result[raw_code] = item  # 也存储为简化 key
        except Exception:
            logger.debug("行情字段解析跳过")
            continue
    return result


def _normalize_code(raw: str) -> str:
    """将腾讯返回的原始代码规范化为统一格式"""
    raw = raw.lower().strip()
    if raw.startswith(("sh", "sz", "bj")):
        return raw
    # 判断市场前缀
    if raw.startswith(("6", "5", "9")):
        return f"sh{raw}"
    else:
        return f"sz{raw}"


async def live_price_poller():
    """实时行情轮询主循环 - 单次批量请求所有符号
    
    每次轮询仅发起 1 次 API 调用，包含：
    - 6只指数（固定）
    - 自选股（最多3只）
    - 用户热查询符号（60s TTL，自动清理）
    """
    global _live_cache_ts
    logger.info(
        f"实时行情轮询v3启动：{LIVE_PRICE_POLL_INTERVAL}s间隔，"
        f"单次批量拉取（指数+自选+热符号），"
        f"限流器={api_limiter.max_rate}req/{api_limiter.time_period}s"
    )
    
    _last_fetch_ts: float | None = None  # 上次实际请求时间（事件循环单调时钟）

    async with httpx.AsyncClient(timeout=5) as client:
        while True:
            try:
                # ── 动态轮询节奏 ──
                # 竞价(9:15起)与连续竞价：实时高频监控
                # 其余时段（盘前/午休/盘后/非交易日）：低频检查，默认沿用上一次收盘缓存
                is_trading = TradingTimeChecker.is_trading_time()
                is_auction = TradingTimeChecker.is_auction_phase()
                realtime_mode = is_trading or is_auction

                if realtime_mode:
                    await asyncio.sleep(LIVE_PRICE_POLL_INTERVAL)
                    should_fetch = True
                else:
                    await asyncio.sleep(_OFF_HOURS_CHECK_SECONDS)
                    now_loop = asyncio.get_event_loop().time()
                    should_fetch = (_last_fetch_ts is None or
                                    (now_loop - _last_fetch_ts) >= _OFF_HOURS_REFRESH_SECONDS)

                if not should_fetch:
                    continue

                # ── 收集本轮需要查询的全部符号 ──
                symbols_to_fetch: set[str] = set()
                
                # 1. 指数（固定6只，已为腾讯格式）
                for idx in TRACKED_INDICES:
                    symbols_to_fetch.add(idx.lower())
                
                # 2. 自选股（从数据库获取，需转换为腾讯格式）
                try:
                    watchlist = await db_service.get_watchlist()
                    for w in watchlist:
                        tc = to_tencent_code(w["symbol"])
                        if tc:
                            symbols_to_fetch.add(tc.lower())
                except Exception as e:
                    logger.warning(f"获取自选列表失败: {e}")
                
                # 3. 热符号（用户查询过的非自选股，自动清理过期）
                now = asyncio.get_event_loop().time()
                expired = [s for s, t in _hot_symbols.items() if t < now]
                for s in expired:
                    _hot_symbols.pop(s, None)
                for sym in _hot_symbols:
                    tc = to_tencent_code(sym)
                    if tc:
                        symbols_to_fetch.add(tc.lower())
                
                if not symbols_to_fetch:
                    continue
                
                # 单次批量请求
                code_str = ",".join(sorted(symbols_to_fetch))
                
                async with api_limiter:
                    resp = await client.get(f"http://qt.gtimg.cn/q={code_str}")
                    if resp.status_code != 200:
                        continue
                
                raw_text = resp.text
                if not raw_text or "pv_none_match" in raw_text:
                    continue
                
                parsed = await _parse_tencent_batch(raw_text)
                
                # 更新内存缓存
                for code, item in parsed.items():
                    _live_cache[code] = {
                        "last_price": item["last_price"],
                        "prev_close": item["prev_close"],
                        "open": item["open"],
                        "high": item["high"],
                        "low": item["low"],
                        "volume": item["volume"],
                        "amount": item["amount"],
                        "turnover": item["turnover"],
                        "pe": item["pe"],
                        "pb": item["pb"],
                        "change": item["change"],
                        "change_pct": item["change_pct"],
                        "name": item["name"],
                    }
                _live_cache_ts = datetime.now().astimezone().isoformat()
                _last_fetch_ts = asyncio.get_event_loop().time()
                
                # 分离指数数据（同步写入 indices._index_cache，供 market_tool 消费）
                from .indices import update_index_cache
                idx_updates = {}
                for idx in TRACKED_INDICES:
                    item = parsed.get(idx.lower())
                    if item:
                        idx_data = {
                            "name": item["name"],
                            "price": item["last_price"],
                            "change_pct": item["change_pct"],
                            "change": item["change"],
                        }
                        _realtime_indices[idx] = {
                            "name": item["name"],
                            "last_price": item["last_price"],
                            "change_pct": item["change_pct"],
                            "change": item["change"],
                        }
                        idx_updates[idx] = idx_data
                if idx_updates:
                    update_index_cache(idx_updates)
                
                # 订单撮合（仅在交易时间）
                if is_trading:
                    try:
                        fills = await match_orders(_live_cache)
                        # 对每笔成交调用 fill callback，完成持仓和余额结算
                        if fills and _fill_callback:
                            for fill in fills:
                                try:
                                    await _fill_callback(fill)
                                except Exception as e:
                                    logger.error(f"成交回调异常 {fill.get('order_id')}: {e}")
                    except Exception as e:
                        logger.debug(f"订单撮合跳过: {e}")
                
            except asyncio.CancelledError:
                logger.info("实时行情轮询服务已停止")
                break
            except Exception:
                logger.error(f"轮询异常: {traceback.format_exc()}")
                await asyncio.sleep(1)  # 异常时稍作等待


async def start_live_poller():
    """启动后台轮询任务"""
    return asyncio.create_task(live_price_poller())
