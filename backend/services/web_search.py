"""通用网页搜索服务（搜索引擎全网搜索）。

在既有「特定财经站点新闻」（东方财富/财联社/新浪）之外，提供面向全网信息的
搜索引擎接入，让市场情报 Agent 不仅能查特定网站的新闻，也能通过搜索引擎获取
更广泛的资讯（行业、主题、政策、个股等）。

数据源：
- 主源：DuckDuckGo HTML（免 API key、免鉴权），解析结果标题/链接/摘要
- 无结果或失败时返回空列表，交由上层（news_service）与财经站点结果合并降级

约束：只做轻量 HTML 抓取与解析，不引入重型爬虫依赖；超时短、失败静默降级。
"""

import logging
import re
from html import unescape
from urllib.parse import unquote

import httpx

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

# DuckDuckGo HTML 端点（GET，免 key）
_DDG_HTML_URL = "https://html.duckduckgo.com/html/"


def _strip_tags(html: str) -> str:
    """去除 HTML 标签，保留纯文本。"""
    return re.sub(r"<[^>]+>", "", html or "").strip()


def _extract_ddg_url(raw_url: str) -> str:
    """解析 DuckDuckGo 结果里的跳转链接（uddg= 参数）。"""
    if not raw_url:
        return ""
    m = re.search(r"uddg=([^&]+)", raw_url)
    if m:
        try:
            return unquote(m.group(1))
        except Exception:
            return raw_url
    return raw_url


def _parse_ddg_html(html_text: str, limit: int) -> list:
    """从 DuckDuckGo HTML 结果页解析出 (title, url, snippet) 列表。"""
    items = []
    # DDG HTML 结果结构：<a class="result__a" href="...">标题</a>
    # 其后紧跟 <a class="result__snippet" ...>摘要</a>
    pattern = re.compile(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>'
        r'.*?<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    for m in pattern.finditer(html_text):
        raw_url = _extract_ddg_url(m.group(1))
        title = unescape(_strip_tags(m.group(2)))
        snippet = unescape(_strip_tags(m.group(3)))
        if not title or not raw_url:
            continue
        items.append(
            {
                "title": title,
                "url": raw_url,
                "source": "web",
                "summary": snippet or title,
                "time": "",
                "symbols": [],
            }
        )
        if len(items) >= limit:
            break
    return items


async def search_web(keyword: str, limit: int = 8) -> list:
    """通过搜索引擎全网搜索关键词，返回结构化结果列表。

    失败或超时返回空列表（不抛异常），由调用方决定是否降级到财经站点搜索。
    """
    keyword = (keyword or "").strip()
    if not keyword:
        return []

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.post(
                _DDG_HTML_URL,
                data={"q": keyword},
                headers={"User-Agent": _UA, "Referer": "https://duckduckgo.com/"},
            )
            resp.raise_for_status()
            items = _parse_ddg_html(resp.text, limit)
            if items:
                logger.info("web search '%s' 命中 %d 条", keyword, len(items))
            return items
    except Exception as e:
        logger.debug("搜索引擎全网搜索失败 keyword=%s: %s", keyword, e)
        return []
