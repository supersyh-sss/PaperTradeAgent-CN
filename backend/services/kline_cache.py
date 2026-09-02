"""K线数据文件缓存 - 避免重复请求API"""

import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

BJT = timezone(timedelta(hours=8))
CACHE_DIR = Path(__file__).parent.parent.parent / "data" / "kline_cache"


def _ensure_cache_dir():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_key(symbol: str, period: str) -> str:
    return f"{symbol}_{period}.json"


def _cache_path(symbol: str, period: str) -> Path:
    return CACHE_DIR / _cache_key(symbol, period)


def _is_daily_cache_valid(cache_time: datetime, now: datetime) -> bool:
    """日K线缓存：同一交易日或次日15:30前有效"""
    today = now.date()
    cache_date = cache_time.date()

    # 同一交易日内的缓存始终有效
    if cache_date == today:
        return True

    # 上一个交易日
    last_td = _last_trading_day(today)
    if cache_date != last_td:
        return False

    # 缓存来自上一交易日：若今天是周末则始终有效
    if today.weekday() >= 5:
        return True

    # 工作日：15:30前有效，之后失效（新数据已出）
    market_close = datetime.combine(today, time(15, 30), tzinfo=BJT)
    return now <= market_close


def _is_intraday_cache_valid(cache_mtime: float, ttl_seconds: int = 300) -> bool:
    """日内缓存：5分钟TTL"""
    age = datetime.now().timestamp() - cache_mtime
    return age < ttl_seconds


def _last_trading_day(d: date) -> date:
    """上一个交易日（含中国节假日调休）"""
    import datetime as dt

    try:
        from chinese_calendar import is_workday

        result = d - dt.timedelta(days=1)
        while not is_workday(result):
            result -= dt.timedelta(days=1)
        return result
    except ImportError:
        # fallback: only skip weekends
        result = d - dt.timedelta(days=1)
        while result.weekday() >= 5:
            result -= dt.timedelta(days=1)
        return result


def get_cached_kline(
    symbol: str, period: str = "day", days: int = 360
) -> list[dict] | None:
    """获取缓存的K线数据"""
    _ensure_cache_dir()
    cache_file = _cache_path(symbol, period)
    if not cache_file.exists():
        return None
    try:
        stat = cache_file.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=BJT)
        now = datetime.now(BJT)
        if period == "day":
            if not _is_daily_cache_valid(mtime, now):
                return None
        else:
            if not _is_intraday_cache_valid(stat.st_mtime):
                return None
        with open(cache_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 返回指定天数
        if isinstance(data, list):
            return data[-days:]
        return data
    except (OSError, json.JSONDecodeError):
        return None


def save_kline_to_cache(symbol: str, period: str, data: list[dict]):
    """保存K线数据到文件缓存"""
    _ensure_cache_dir()
    cache_file = _cache_path(symbol, period)
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, default=str)
    except OSError:
        pass


def invalidate_kline_cache(symbol: str, period: str = "day"):
    """使指定K线缓存失效"""
    cache_file = _cache_path(symbol, period)
    if cache_file.exists():
        cache_file.unlink()
