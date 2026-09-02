"""新闻抓取服务 - 从东方财富、财联社、新浪财经等源实时获取财经新闻

数据源：
- 东方财富栏目新闻（column=350 国内 / 351 国际）
- 东方财富全站搜索（关键词/个股名称，支持翻页获取更早历史）
- 新浪财经滚动新闻
- 财联社电报（降级源）

Cache: 60 秒 TTL，避免频繁调用触发限流
"""

import asyncio
import json as json_mod
import logging
import re
import time as _time
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

BJT = timezone(timedelta(hours=8))

# 新闻缓存
_cache: dict[str, dict] = {}  # key -> {"data": [...], "ts": timestamp}

_CACHE_TTL = 60  # 秒

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def _is_cache_valid(key: str) -> bool:
    entry = _cache.get(key)
    if not entry:
        return False
    return (_time.time() - entry["ts"]) < _CACHE_TTL


def _set_cache(key: str, data: list):
    _cache[key] = {"data": data, "ts": _time.time()}


def _get_cache(key: str) -> list:
    entry = _cache.get(key)
    if entry and (_time.time() - entry["ts"]) < _CACHE_TTL:
        return entry["data"]
    return []


def _norm_time(tm) -> str:
    """把各种时间格式归一化为 'YYYY-MM-DD HH:MM'；失败返回当前北京时间。"""
    now = datetime.now(BJT).strftime("%Y-%m-%d %H:%M")
    if not tm:
        return now
    if isinstance(tm, (int, float)):
        try:
            return datetime.fromtimestamp(tm, tz=BJT).strftime("%Y-%m-%d %H:%M")
        except (ValueError, TypeError, OSError):
            return now
    s = str(tm).strip()
    if not s:
        return now
    try:
        if "T" in s:
            dt = datetime.fromisoformat(s)
            return dt.astimezone(BJT).strftime("%Y-%m-%d %H:%M")
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d %H:%M")
    except Exception:
        return s[:16]


def _parse_jsonp(text: str) -> dict:
    """解析 JSONP 响应（如 cb({...})）。"""
    if not text:
        return {}
    start = text.find("(")
    end = text.rfind(")")
    if start == -1 or end == -1 or end <= start:
        return {}
    try:
        return json_mod.loads(text[start + 1 : end])
    except Exception:
        return {}


def _dedupe_sort(items: list) -> list:
    """按标题去重并按时间倒序排序。"""
    seen = set()
    out = []
    for item in items:
        key = (item.get("title", "") or "").strip()[:60]
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    out.sort(key=lambda x: x.get("time", "") or "", reverse=True)
    return out


# ─────────────────────────── 数据源 ───────────────────────────


async def _fetch_eastmoney_headlines() -> list:
    """东方财富栏目新闻（国内经济 350 + 国际 351）"""
    items = []
    for column in (350, 351):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://np-listapi.eastmoney.com/comm/web/getNewsByColumns",
                    params={
                        "client": "web",
                        "biz": "web_news_col",
                        "column": column,
                        "needInteractData": "0",
                        "pageIndex": "1",
                        "pageSize": "15",
                        "req_trace": "1",
                    },
                    headers={"Referer": "https://www.eastmoney.com/"},
                )
                data = resp.json()
                records = data.get("data", {}).get("list", []) or []
                for item in records:
                    items.append(
                        {
                            "title": item.get("title", ""),
                            "source": item.get("mediaName", "东方财富"),
                            "url": item.get("url", "") or item.get("uniqueUrl", ""),
                            "time": _norm_time(item.get("showTime", "")),
                            "summary": item.get("summary", "") or item.get("title", ""),
                            "symbols": [],
                        }
                    )
        except Exception:
            logger.debug("东方财富栏目新闻抓取失败 column=%s", column, exc_info=True)
    return items


async def _fetch_eastmoney_search(
    keyword: str, page: int = 1, page_size: int = 10
) -> list:
    """东方财富全站资讯关键词搜索（cmsArticleWebOld）。"""
    items = []
    if not keyword:
        return items
    param = {
        "uid": "",
        "keyword": keyword,
        "type": ["cmsArticleWebOld"],
        "client": "web",
        "clientType": "web",
        "clientVersion": "curr",
        "param": {
            "cmsArticleWebOld": {
                "searchScope": "default",
                "sort": "default",
                "pageIndex": page,
                "pageSize": page_size,
                "preTag": "<em>",
                "postTag": "</em>",
            },
        },
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://search-api-web.eastmoney.com/search/jsonp",
                params={
                    "cb": "cb",
                    "param": json_mod.dumps(
                        param, ensure_ascii=False, separators=(",", ":")
                    ),
                },
                headers={"Referer": "https://so.eastmoney.com/", "User-Agent": _UA},
            )
            data = _parse_jsonp(resp.text)
            records = data.get("result", {}).get("cmsArticleWebOld", []) or []
            for item in records:
                title = re.sub(r"</?em>", "", item.get("title", ""))
                content = re.sub(r"</?em>", "", item.get("content", ""))
                items.append(
                    {
                        "title": title,
                        "source": item.get("mediaName", "东方财富"),
                        "url": item.get("url", ""),
                        "time": _norm_time(item.get("date", "")),
                        "summary": content,
                        "symbols": [],
                    }
                )
    except Exception:
        logger.debug("东方财富关键词搜索失败 keyword=%s", keyword, exc_info=True)
    return items


async def _fetch_cls_news() -> list:
    """财联社电报（降级源）"""
    items = []
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                "https://www.cls.cn/api/sw?app=CailianpressWeb&os=web&sv=7.7.5",
                headers={"Referer": "https://www.cls.cn/telegraph"},
            )
            data = resp.json()
            roll_data = data.get("data", {}).get("roll_data", [])
            for item in (roll_data or [])[:10]:
                items.append(
                    {
                        "title": item.get("title", ""),
                        "source": "财联社",
                        "url": f"https://www.cls.cn/detail/{item.get('id', '')}",
                        "time": _norm_time(item.get("ctime", 0)),
                        "summary": item.get("brief", "") or item.get("title", ""),
                        "symbols": [],
                    }
                )
    except Exception:
        logger.debug("财联社新闻抓取失败", exc_info=True)
    return items


async def _fetch_sina_finance() -> list:
    """新浪财经滚动新闻"""
    items = []
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                "https://feed.mix.sina.com.cn/api/roll/get",
                params={"pageid": 153, "lid": "2509", "num": 20},
                headers={"Referer": "https://finance.sina.com.cn/"},
            )
            data = resp.json()
            for item in data.get("result", {}).get("data", [])[:15]:
                ctime = item.get("ctime", "")
                tm = (
                    _norm_time(int(ctime))
                    if str(ctime).isdigit()
                    else _norm_time(ctime)
                )
                items.append(
                    {
                        "title": item.get("title", ""),
                        "source": "新浪财经",
                        "url": item.get("url", ""),
                        "time": tm,
                        "summary": item.get("intro", "") or item.get("title", ""),
                        "symbols": [],
                    }
                )
    except Exception:
        logger.debug("新浪财经新闻抓取失败", exc_info=True)
    return items


async def _fetch_eastmoney_stock_news(symbol: str) -> list:
    """个股新闻：优先用股票名称搜索，其次用代码搜索。"""
    items = []
    try:
        from .stock_lookup import resolve
        from .symbol import pure_code

        code = pure_code(symbol)
        name = ""
        try:
            info = resolve(code)
            name = (info or {}).get("name", "")
        except Exception:
            name = ""

        keywords = []
        if name and name != code:
            keywords.append(name)
        keywords.append(code)

        for kw in keywords:
            got = await _fetch_eastmoney_search(kw, page=1, page_size=10)
            for it in got:
                it["symbols"] = [code]
            items.extend(got)
            if len(items) >= 10:
                break
    except Exception:
        logger.debug("东方财富个股新闻抓取失败 symbol=%s", symbol, exc_info=True)
    return items


# ═══════════════════════════════════════════════════════════
#  SQLite 持久化
# ═══════════════════════════════════════════════════════════


async def save_news_to_db(news_items: list) -> int:
    """持久化新闻到 SQLite，按 (title, source) 去重"""
    if not news_items:
        return 0
    try:
        from ..services.db import get_db

        values = []
        for item in news_items:
            symbols_str = (
                ",".join(item.get("symbols", [])) if item.get("symbols") else None
            )
            values.append(
                (
                    item.get("title", ""),
                    item.get("source", ""),
                    item.get("url", ""),
                    item.get("summary", "") or item.get("content", ""),
                    symbols_str,
                    item.get("sentiment_score", 0),
                    item.get("time", ""),
                )
            )

        db = await get_db()
        try:
            before = db.total_changes
            await db.executemany(
                """INSERT OR IGNORE INTO market_news
                   (title, source, url, content, symbols, sentiment_score, crawled_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                values,
            )
            await db.commit()
            inserted = db.total_changes - before
            if inserted:
                logger.debug(f"持久化新闻 {inserted}/{len(values)} 条到 SQLite")
            return inserted
        finally:
            await db.close()
    except Exception as e:
        logger.warning(f"持久化新闻到数据库失败: {e}")
        return 0


async def get_cached_news(symbol: str | None = None, limit: int = 20) -> list:
    """从 SQLite 读取已持久化的新闻（历史回退用）"""
    try:
        from ..services.db import get_db

        db = await get_db()
        try:
            if symbol:
                cursor = await db.execute(
                    """SELECT * FROM market_news
                       WHERE symbols LIKE ?
                       ORDER BY crawled_at DESC LIMIT ?""",
                    (f"%{symbol}%", limit),
                )
            else:
                cursor = await db.execute(
                    """SELECT * FROM market_news
                       ORDER BY crawled_at DESC LIMIT ?""",
                    (limit,),
                )
            rows = await cursor.fetchall()
            result = []
            for row in rows:
                d = dict(row)
                result.append(
                    {
                        "title": d.get("title", ""),
                        "source": d.get("source", ""),
                        "url": d.get("url", ""),
                        "time": d.get("crawled_at", ""),
                        "summary": d.get("content", ""),
                        "symbols": [
                            s.strip()
                            for s in d.get("symbols", "").split(",")
                            if s.strip()
                        ]
                        if d.get("symbols")
                        else [],
                        "sentiment_score": d.get("sentiment_score", 0),
                        "created_at": d.get("created_at", ""),
                    }
                )
            return result
        finally:
            await db.close()
    except Exception as e:
        logger.warning(f"读取持久化新闻失败: {e}")
        return []


# 公开 API


async def get_market_news() -> dict:
    """获取综合市场新闻

    返回: {"data": [...], "count": N, "source": "live"|"fallback"}
    """
    cache_key = "market_news"
    cached = _get_cache(cache_key)
    if cached:
        return {"data": cached, "count": len(cached), "source": "live (cached)"}

    results = await asyncio.gather(
        _fetch_eastmoney_headlines(),
        _fetch_cls_news(),
        _fetch_sina_finance(),
        return_exceptions=True,
    )

    all_news = []
    for res in results:
        if isinstance(res, list):
            all_news.extend(res)

    deduped = _dedupe_sort(all_news)

    # 最近新闻为空时，回退到本地 SQLite 历史记录
    if not deduped:
        cached_news = await get_cached_news(None, limit=30)
        cached_news = [n for n in cached_news if n.get("url")]
        deduped = _dedupe_sort(cached_news)

    if not deduped:
        logger.warning("所有新闻源均无法访问，返回空结果")
        deduped = [
            {
                "title": "暂时无法获取实时新闻数据",
                "source": "系统",
                "url": "",
                "time": datetime.now(BJT).strftime("%Y-%m-%d %H:%M"),
                "summary": "网络连接异常或所有新闻源暂时不可用，请稍后再试",
                "symbols": [],
            }
        ]
        source = "fallback"
    else:
        source = "live"

    _set_cache(cache_key, deduped)
    if source == "live":
        asyncio.ensure_future(save_news_to_db(deduped))
    return {"data": deduped, "count": len(deduped), "source": source}


async def get_stock_news(symbol: str) -> dict:
    """获取指定个股新闻

    Args:
        symbol: 股票代码（纯6位或 sh/sz/bj 前缀格式）

    Returns: {"data": [...], "count": N, "symbol": symbol}
    """
    cache_key = f"stock_news_{symbol}"
    cached = _get_cache(cache_key)
    if cached:
        return {"data": cached, "count": len(cached), "symbol": symbol}

    results = await asyncio.gather(
        _fetch_eastmoney_stock_news(symbol),
        return_exceptions=True,
    )

    all_news = []
    for res in results:
        if isinstance(res, list):
            all_news.extend(res)

    all_news = _dedupe_sort(all_news)

    # 最近新闻不足时，翻页搜索更久远的历史新闻
    if len(all_news) < 5:
        try:
            from .stock_lookup import resolve
            from .symbol import pure_code

            code = pure_code(symbol)
            info = resolve(code) or {}
            kw = info.get("name") or code
            for page in (2, 3):
                older = await _fetch_eastmoney_search(kw, page=page, page_size=10)
                for it in older:
                    it["symbols"] = [code]
                all_news.extend(older)
                all_news = _dedupe_sort(all_news)
                if len(all_news) >= 10:
                    break
        except Exception as e:
            logger.debug("历史新闻翻页搜索失败 symbol=%s: %s", symbol, e)

    # 仍不足时回退到本地 SQLite 历史
    if len(all_news) < 3:
        try:
            cached_news = await get_cached_news(symbol, limit=20)
            cached_news = [n for n in cached_news if n.get("url")]
            all_news.extend(cached_news)
            all_news = _dedupe_sort(all_news)
        except Exception as e:
            logger.debug("本地历史新闻回退失败 symbol=%s: %s", symbol, e)

    if not all_news:
        all_news = [
            {
                "title": f"暂无 {symbol} 相关新闻",
                "source": "系统",
                "url": "",
                "time": datetime.now(BJT).strftime("%Y-%m-%d %H:%M"),
                "summary": "未找到该股票相关的最新新闻",
                "symbols": [symbol],
            }
        ]
        source = "fallback"
    else:
        source = "live"

    _set_cache(cache_key, all_news)
    if source == "live":
        asyncio.ensure_future(save_news_to_db(all_news))
    return {"data": all_news, "count": len(all_news), "symbol": symbol}


async def search_news(keyword: str, limit: int = 10) -> dict:
    """按关键词搜索财经新闻（不足时翻页获取更早历史）

    Args:
        keyword: 搜索关键词，如 "人工智能"、"新能源汽车"、"贵州茅台"
        limit: 返回条数

    Returns: {"data": [...], "count": N, "keyword": keyword}
    """
    keyword = (keyword or "").strip()
    if not keyword:
        return {"data": [], "count": 0, "keyword": keyword}

    cache_key = f"search_news_{keyword}"
    cached = _get_cache(cache_key)
    if cached:
        return {
            "data": cached[:limit],
            "count": min(len(cached), limit),
            "keyword": keyword,
        }

    # 并行获取：东方财富关键词搜索 + 搜索引擎全网搜索（跨站资讯覆盖）
    from .web_search import search_web

    results = await asyncio.gather(
        _fetch_eastmoney_search(keyword, page=1, page_size=20),
        search_web(keyword, limit=limit),
        return_exceptions=True,
    )
    all_news = []
    for res in results:
        if isinstance(res, list):
            all_news.extend(res)

    # 不足时翻页获取更早历史（仅财经源翻页，搜索引擎结果已固定）
    if len(all_news) < limit:
        for page in (2, 3):
            older = await _fetch_eastmoney_search(keyword, page=page, page_size=20)
            all_news.extend(older)
            all_news = _dedupe_sort(all_news)
            if len(all_news) >= limit * 2:
                break

    all_news = _dedupe_sort(all_news)[:limit]
    _set_cache(cache_key, all_news)
    if all_news:
        asyncio.ensure_future(save_news_to_db(all_news))
    return {"data": all_news, "count": len(all_news), "keyword": keyword}


async def get_breaking_news() -> dict:
    """获取突发/紧急新闻（财联社电报为主）"""
    cache_key = "breaking_news"
    cached = _get_cache(cache_key)
    if cached:
        return {"data": cached, "count": len(cached)}

    cls_news = await _fetch_cls_news()

    now = _time.time()
    breaking = []
    for item in cls_news:
        try:
            dt = datetime.strptime(item.get("time", ""), "%Y-%m-%d %H:%M")
            age = now - dt.replace(tzinfo=BJT).timestamp()
            item["urgency"] = "breaking" if age < 900 else "recent"
        except (ValueError, TypeError, OSError):
            item["urgency"] = "recent"
        breaking.append(item)

    if not breaking:
        breaking = [
            {
                "title": "暂无突发新闻",
                "source": "系统",
                "url": "",
                "time": datetime.now(BJT).strftime("%Y-%m-%d %H:%M"),
                "summary": "当前时段无突发新闻",
                "symbols": [],
                "urgency": "info",
            }
        ]

    _set_cache(cache_key, breaking)
    return {"data": breaking, "count": len(breaking)}
