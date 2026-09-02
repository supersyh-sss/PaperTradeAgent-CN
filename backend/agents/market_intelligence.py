"""市场动态感知官 Agent - 消息面分析"""

import json as json_mod
import logging
from datetime import datetime

from ..services.agent_memory import (
    compute_query_hash,
    get_agent_memory,
    save_agent_memory,
)
from ..services.indices import TRACKED_INDICES
from ..services.llm import deepseek
from ..services.market_tool import get_index_overview
from ..services.trading_time import TradingTimeChecker
from .prompts import AGENT_PROFILES, MARKET_INTELLIGENCE_SYSTEM
from .state import AgentState
from .utils import maybe_attach_followup

logger = logging.getLogger(__name__)


# 已知股票的重大事件情报（模拟数据 - 实际应抓取东方财富/巨潮资讯）
STOCK_INTEL_DB = {
    "sh600519": {
        "sentiment_score": 0.35,
        "sentiment_label": "偏正面",
        "news": [
            {
                "title": "贵州茅台发布半年报，净利润同比增长15%",
                "source": "巨潮资讯",
                "credibility": 0.95,
                "url": "http://www.cninfo.com.cn/new/disclosure/detail?stockCode=600519",
            },
            {
                "title": "茅台启动新一轮渠道改革，直营比例有望提升",
                "source": "证券时报",
                "credibility": 0.85,
                "url": "https://www.stcn.com/article/detail/xxx.html",
            },
            {
                "title": "北向资金连续3日加仓贵州茅台",
                "source": "东方财富",
                "credibility": 0.75,
                "url": "https://data.eastmoney.com/hsgtcg/StockHdDetail.aspx?stockCode=600519",
            },
        ],
        "announcements": [],
        "impact": {
            "direction": "利好",
            "strength": "moderate",
            "reason": "业绩稳健增长，机构看好",
        },
        "risk_alerts": [],
    },
    "sz300750": {
        "sentiment_score": 0.20,
        "sentiment_label": "偏正面",
        "news": [
            {
                "title": "宁德时代发布新一代钠离子电池，能量密度提升30%",
                "source": "财联社",
                "credibility": 0.80,
                "url": "https://www.cls.cn/detail/xxx",
            },
            {
                "title": "宁德时代与特斯拉达成新合作协议",
                "source": "东方财富",
                "credibility": 0.75,
                "url": "https://data.eastmoney.com/stockdata/300750.html",
            },
        ],
        "announcements": [],
        "impact": {
            "direction": "利好",
            "strength": "moderate",
            "reason": "技术突破+新客户合作",
        },
        "risk_alerts": [],
    },
    "sz002594": {
        "sentiment_score": 0.15,
        "sentiment_label": "中性偏正面",
        "news": [
            {
                "title": "比亚迪8月新能源汽车销量创新高",
                "source": "证券时报",
                "credibility": 0.85,
                "url": "https://www.stcn.com/article/detail/xxx.html",
            },
        ],
        "announcements": [],
        "impact": {"direction": "中性", "strength": "weak", "reason": "消息面平静"},
        "risk_alerts": [],
    },
    "sz000858": {
        "sentiment_score": -0.10,
        "sentiment_label": "中性",
        "news": [],
        "announcements": [],
        "impact": {"direction": "中性", "strength": "weak", "reason": "消息面平静"},
        "risk_alerts": [],
    },
    "sh600036": {
        "sentiment_score": 0.10,
        "sentiment_label": "中性",
        "news": [
            {
                "title": "招商银行零售业务稳健增长",
                "source": "上海证券报",
                "credibility": 0.85,
                "url": "https://www.cnstock.com/",
            },
        ],
        "announcements": [],
        "impact": {"direction": "中性", "strength": "weak", "reason": "消息面平静"},
        "risk_alerts": [],
    },
    "sh601318": {
        "sentiment_score": -0.05,
        "sentiment_label": "中性",
        "news": [],
        "announcements": [],
        "impact": {"direction": "中性", "strength": "weak", "reason": "消息面平静"},
        "risk_alerts": [],
    },
}


async def market_intelligence_node(state: AgentState) -> AgentState:
    """市场动态感知官节点：个股消息面分析 或 市场整体概览"""
    symbol = state.get("active_symbol")
    name = state.get("active_name", "") or symbol

    if not symbol:
        # 无个股 → 生成市场整体概览
        return await _market_overview_node(state)

    # 尝试从缓存/数据库获取
    intel = STOCK_INTEL_DB.get(symbol)

    # 尝试从实时新闻服务获取个股新闻
    real_news_items = []
    try:
        from ..services.news_service import get_stock_news

        news_result = await get_stock_news(symbol)
        if news_result and news_result.get("data"):
            real_news = news_result["data"]
            # 过滤掉无新闻的占位项
            real_news = [n for n in real_news if "暂无" not in n.get("title", "")]
            if real_news:
                real_news_items = real_news
                logger.info(f"获取到 {symbol} 实时新闻 {len(real_news_items)} 条")
    except Exception as e:
        logger.debug(f"实时新闻获取失败(symbol={symbol}): {e}")

    # 如果获取到实时新闻，用其替代硬编码模拟数据（确保展示真实链接）
    if real_news_items:
        intel = {
            "sentiment_score": 0.0,
            "sentiment_label": "中性",
            "news": [
                {
                    "title": n.get("title", ""),
                    "source": n.get("source", ""),
                    "credibility": 0.70,
                    "url": n.get("url", ""),
                    "time": n.get("time", ""),
                }
                for n in real_news_items
            ],
            "announcements": [],
            "impact": {
                "direction": "中性",
                "strength": "weak",
                "reason": "基于实时新闻数据",
            },
            "risk_alerts": [],
            "source": "live",
        }

    if intel is None:
        # 未知股票，返回默认中性评估
        intel = {
            "sentiment_score": 0.0,
            "sentiment_label": "中性",
            "news": [],
            "announcements": [],
            "impact": {
                "direction": "中性",
                "strength": "weak",
                "reason": "暂无相关新闻数据",
            },
            "risk_alerts": [],
        }

    state["market_intelligence"] = intel
    state["sentiment_score"] = intel.get("sentiment_score", 0.0)
    state["news_summary"] = intel.get("summary", "")
    state["risk_alerts"] = intel.get("risk_alerts", [])

    # LLM 分析
    news_list = intel.get("news", [])
    risk_alerts = intel.get("risk_alerts", [])

    lines = [f"扫了眼{name or symbol}的消息面："]

    # Sentiment
    sentiment_label = intel.get("sentiment_label", "中性")
    sentiment_score = intel.get("sentiment_score", 0)

    # News — only show if there's actual news
    if news_list:
        sentiments = (
            "偏多"
            if sentiment_score > 0.2
            else "偏空"
            if sentiment_score < -0.2
            else "中性"
        )
        lines.append(
            f"舆情{sentiments}（{sentiment_score:+.2f}），有{len(news_list)}条相关动态"
        )
        for n in news_list[:4]:
            title = (n.get("title", "") or "").strip()
            url = (n.get("url", "") or "").strip()
            source = (n.get("source", "") or "").strip()
            pub_time = (n.get("time", "") or "").strip()
            time_part = f" · {pub_time}" if pub_time else ""
            if url:
                lines.append(f"  · [{title}]({url}) · {source}{time_part}")
            else:
                lines.append(f"  · {title} · {source}{time_part}")
    else:
        # No news = skip the section entirely, just note sentiment
        if abs(sentiment_score) < 0.1:
            lines.append("最近没什么大新闻，盘面平稳")
        else:
            lines.append(f"舆情{sentiment_label}，但没什么具体消息")

    # Only show impact if meaningful
    impact = intel.get("impact", {})
    impact_dir = impact.get("direction", "中性")
    if impact_dir != "中性":
        lines.append(
            f"影响：{impact_dir}，{impact.get('strength', 'weak')}级（{impact.get('reason', '')}）"
        )

    # Risk alerts — only meaningful ones
    if risk_alerts:
        for r in risk_alerts[:1]:
            lines.append(f"⚠ {r.get('title', '')}")

    try:
        user_input = state.get("user_input", "")
        user_id = state.get("user_id", "default")
        agent_key = "market_intelligence"
        query_hash = compute_query_hash(user_input, agent_key, symbol)
        is_trading = TradingTimeChecker.is_trading_time()

        # 盘中始终调用 LLM，盘后/非交易时段优先使用缓存
        cached = None
        if not is_trading:
            cached = await get_agent_memory(
                user_id, agent_key, symbol, query_hash, query=user_input
            )

        if cached and not is_trading:
            try:
                llm_result = json_mod.loads(cached)
                state["intelligence_assessment"] = llm_result
            except Exception:
                cached = None

        if cached is None or is_trading:
            # A4：优先多轮工具编排，解析失败回退到预取数据单次调用
            llm_result = await _intel_llm_with_tools(state, symbol, name)
            if llm_result.get("parse_error"):
                llm_result = await _intel_llm_legacy(state, intel, symbol)
            if llm_result.get("parse_error"):
                raise ValueError("LLM returned non-JSON")
            state["intelligence_assessment"] = llm_result

            # 保存到 agent_memory
            await save_agent_memory(
                user_id,
                agent_key,
                symbol,
                query_hash,
                json_mod.dumps(llm_result, ensure_ascii=False, default=str),
                query=user_input,
            )

        # Add LLM analysis to log
        summary = llm_result.get("impact_summary", "")
        if summary:
            lines.append(f"  研判：{summary}")
    except Exception:
        state["intelligence_assessment"] = None

    agent_log_content = "\n".join(lines)

    # Emit agent_log
    agent_log = {
        "agent": "market_intelligence",
        "emoji": AGENT_PROFILES["market_intelligence"]["emoji"],
        "name_cn": AGENT_PROFILES["market_intelligence"]["name_cn"],
        "color": AGENT_PROFILES["market_intelligence"]["color"],
        "content": agent_log_content,
        "timestamp": datetime.now().astimezone().isoformat(),
    }
    agent_log = maybe_attach_followup(agent_log, "market_intelligence")
    state.setdefault("agent_logs", []).append(agent_log)

    return state


async def _market_overview_node(state: AgentState) -> AgentState:
    """市场动态感知官：大盘概览分支"""
    overview = await get_index_overview()
    state["market_intelligence"] = overview
    sentiment = overview.get("sentiment", {})
    state["sentiment_score"] = sentiment.get("score", 0.0)

    summary = overview.get("summary", {})
    indices = overview.get("indices", {})
    status = overview.get("status", {})

    lines = ["市场整体扫描完成：", ""]
    lines.append(f"- 交易状态：{status.get('detail', '未知')}")
    lines.append(
        f"- 指数涨跌：涨 {summary.get('up_count', 0)} / 跌 {summary.get('down_count', 0)} / 平 {summary.get('flat_count', 0)}"
    )
    lines.append(
        f"- 市场情绪：{sentiment.get('sentiment', 'unknown')}（评分 {sentiment.get('score', 0):+.2f}）"
    )

    if indices:
        lines.append("- 主要指数：")
        for sym, name in TRACKED_INDICES.items():
            d = indices.get(sym, {})
            if d:
                pct = d.get("change_pct", 0)
                lines.append(f"   {name}：{d.get('price', 'N/A')} ({pct:+.2f}%)")

    leaders = [d for d in overview.get("leaders", []) if d.get("change_pct", 0) > 0]
    if leaders:
        lines.append("- 领涨指数：")
        for d in leaders:
            lines.append(
                f"   {d.get('name', d.get('symbol', ''))} {d.get('change_pct', 0):+.2f}%"
            )
    else:
        lines.append("- 领涨指数：无（当前无上涨指数）")

    laggards = [d for d in overview.get("laggards", []) if d.get("change_pct", 0) < 0]
    if laggards:
        lines.append("- 领跌指数：")
        for d in laggards:
            lines.append(
                f"   {d.get('name', d.get('symbol', ''))} {d.get('change_pct', 0):+.2f}%"
            )
    else:
        lines.append("- 领跌指数：无（当前无下跌指数）")

    # 附加市场要闻（带可点击链接）
    try:
        from ..services.news_service import get_market_news

        mnews = await get_market_news()
        mnews_items = [n for n in mnews.get("data", []) if n.get("url")]
        if mnews_items:
            lines.append("- 市场要闻：")
            for n in mnews_items[:4]:
                title = (n.get("title", "") or "").strip()
                url = (n.get("url", "") or "").strip()
                source = (n.get("source", "") or "").strip()
                lines.append(f"   [{title}]({url}) · {source}")
    except Exception as e:
        logger.debug(f"市场新闻获取失败: {e}")

    # 注入大盘概览给 LLM 做简单研判（复用 MARKET_INTELLIGENCE_SYSTEM 的 JSON 格式）
    try:
        user_input = state.get("user_input", "")
        user_id = state.get("user_id", "default")
        agent_key = "market_intelligence"
        query_hash = compute_query_hash(user_input, agent_key, "market_overview")
        is_trading = TradingTimeChecker.is_trading_time()

        cached = None
        if not is_trading:
            cached = await get_agent_memory(
                user_id, agent_key, "market_overview", query_hash, query=user_input
            )

        if cached and not is_trading:
            try:
                llm_result = json_mod.loads(cached)
                state["intelligence_assessment"] = llm_result
            except Exception:
                cached = None

        if cached is None or is_trading:
            # A4：优先多轮工具编排，解析失败回退到预取数据单次调用
            llm_result = await _market_overview_llm_with_tools(state)
            if llm_result.get("parse_error"):
                llm_result = await _market_overview_llm_legacy(state, overview)
            if llm_result.get("parse_error"):
                raise ValueError("LLM returned non-JSON")
            state["intelligence_assessment"] = llm_result

            await save_agent_memory(
                user_id,
                agent_key,
                "market_overview",
                query_hash,
                json_mod.dumps(llm_result, ensure_ascii=False, default=str),
                query=user_input,
            )

        summary_text = llm_result.get("impact_summary", "")
        if summary_text:
            lines.append(f"  研判：{summary_text}")
    except Exception:
        state["intelligence_assessment"] = None

    agent_log = {
        "agent": "market_intelligence",
        "emoji": AGENT_PROFILES["market_intelligence"]["emoji"],
        "name_cn": AGENT_PROFILES["market_intelligence"]["name_cn"],
        "color": AGENT_PROFILES["market_intelligence"]["color"],
        "content": "\n".join(lines),
        "timestamp": datetime.now().astimezone().isoformat(),
    }
    agent_log = maybe_attach_followup(agent_log, "market_intelligence")
    state.setdefault("agent_logs", []).append(agent_log)
    return state


def _build_market_overview_summary(overview: dict) -> str:
    """为 LLM 构造市场概览文本"""
    lines = ["Market overview (A-Share indices):"]
    sentiment = overview.get("sentiment", {})
    lines.append(
        f"Sentiment: {sentiment.get('sentiment', 'unknown')} (score {sentiment.get('score', 0)})"
    )
    summary = overview.get("summary", {})
    lines.append(
        f"Index breadth: up={summary.get('up_count', 0)}, down={summary.get('down_count', 0)}, flat={summary.get('flat_count', 0)}"
    )
    for sym, d in overview.get("indices", {}).items():
        lines.append(
            f"{sym}: price={d.get('price')}, change_pct={d.get('change_pct')}%"
        )
    return "\n".join(lines)


def _build_intel_summary(intel: dict, symbol: str) -> str:
    """Build a text summary of market intelligence for LLM input."""
    lines = [f"Stock: {symbol}"]
    news = intel.get("news", [])
    if news:
        lines.append(f"News items ({len(news)}):")
        for n in news:
            pub_time = n.get("time", "")
            time_part = f" @ {pub_time}" if pub_time else ""
            lines.append(
                f"- [{n.get('source', 'unknown')}] {n.get('title', '')} "
                f"(credibility: {n.get('credibility', 0.5)}){time_part}"
            )
    else:
        lines.append("News items: none")

    announcements = intel.get("announcements", [])
    if announcements:
        lines.append(f"Announcements ({len(announcements)}):")
        for a in announcements:
            lines.append(f"- {a}")
    else:
        lines.append("Announcements: none")

    impact = intel.get("impact", {})
    lines.append(
        f"Cached impact: direction={impact.get('direction', 'neutral')}, "
        f"strength={impact.get('strength', 'weak')}, "
        f"reason={impact.get('reason', 'N/A')}"
    )

    risk_alerts = intel.get("risk_alerts", [])
    if risk_alerts:
        lines.append("Risk alerts:")
        for r in risk_alerts:
            lines.append(f"- [{r.get('level', '')}] {r.get('title', '')}")

    return "\n".join(lines)


async def _intel_llm_with_tools(state: AgentState, symbol: str, name: str) -> dict:
    """A4：多轮工具编排，LLM 自主决定查询个股新闻/市场概览。失败返回 parse_error 标记。"""
    from .tool_agent import parse_agent_json, run_tool_agent
    from .tools import TOOLS_BY_AGENT

    user_input = state.get("user_input", "")
    history_summary = state.get("history_summary", "")
    strategy = state.get("strategy_direction", "")
    current_time = state.get("current_time", "")

    ctx_lines = [f"User asked: {user_input}", f"Target stock: {symbol} ({name or ''})"]
    if history_summary:
        ctx_lines.append(f"Conversation context: {history_summary}")
    if strategy:
        ctx_lines.append(f"Orchestrator reasoning: {strategy}")
    if current_time:
        ctx_lines.append(f"Current system time: {current_time}")
    user_prompt = "\n".join(ctx_lines)

    try:
        content, trace = await run_tool_agent(
            deepseek,
            MARKET_INTELLIGENCE_SYSTEM,
            user_prompt,
            TOOLS_BY_AGENT["market_intelligence"],
        )
        if trace:
            logger.info("intel tool trace: %s", [t["tool"] for t in trace])
        return parse_agent_json(content)
    except Exception as e:
        logger.warning("intel tool loop exception: %s", e)
        return {"raw": str(e), "parse_error": True}


async def _intel_llm_legacy(state: AgentState, intel: dict, symbol: str) -> dict:
    """A4 降级：预取新闻数据 + 单次 LLM 调用（原逻辑）"""
    user_input = state.get("user_input", "")
    history_summary = state.get("history_summary", "")
    strategy = state.get("strategy_direction", "")
    current_time = state.get("current_time", "")
    quant = state.get("quant_assessment")

    intel_text = _build_intel_summary(intel, symbol)
    time_prefix = f"Current system time: {current_time}\n\n" if current_time else ""
    ctx_lines = [f"User asked: {user_input}"]
    if history_summary:
        ctx_lines.append(f"Conversation context: {history_summary}")
    if strategy:
        ctx_lines.append(f"Orchestrator reasoning: {strategy}")
    if quant:
        ctx_lines.append(
            f"Quant researcher found: trend={quant.get('trend_assessment', '?')}, strength={quant.get('strength_rating', '?')}/10, summary={quant.get('summary', '')}"
        )
    ctx_prefix = "\n".join(ctx_lines) + "\n\n"
    messages = [
        {"role": "system", "content": MARKET_INTELLIGENCE_SYSTEM},
        {"role": "user", "content": ctx_prefix + time_prefix + intel_text},
    ]
    return await deepseek.chat_json(messages, temperature=0.1, max_tokens=1024)


async def _market_overview_llm_with_tools(state: AgentState) -> dict:
    """A4：大盘概览分支多轮工具编排，LLM 自主调用 get_market_overview。"""
    from .tool_agent import parse_agent_json, run_tool_agent
    from .tools import TOOLS_BY_AGENT

    user_input = state.get("user_input", "")
    history_summary = state.get("history_summary", "")
    strategy = state.get("strategy_direction", "")
    current_time = state.get("current_time", "")

    ctx_lines = [f"User asked: {user_input}", "Target: A-share market overview"]
    if history_summary:
        ctx_lines.append(f"Conversation context: {history_summary}")
    if strategy:
        ctx_lines.append(f"Orchestrator reasoning: {strategy}")
    if current_time:
        ctx_lines.append(f"Current system time: {current_time}")
    user_prompt = "\n".join(ctx_lines)

    try:
        content, trace = await run_tool_agent(
            deepseek,
            MARKET_INTELLIGENCE_SYSTEM,
            user_prompt,
            TOOLS_BY_AGENT["market_intelligence"],
        )
        if trace:
            logger.info("market overview tool trace: %s", [t["tool"] for t in trace])
        return parse_agent_json(content)
    except Exception as e:
        logger.warning("market overview tool loop exception: %s", e)
        return {"raw": str(e), "parse_error": True}


async def _market_overview_llm_legacy(state: AgentState, overview: dict) -> dict:
    """A4 降级：预取大盘概览数据 + 单次 LLM 调用（原逻辑）"""
    user_input = state.get("user_input", "")
    history_summary = state.get("history_summary", "")
    strategy = state.get("strategy_direction", "")
    current_time = state.get("current_time", "")

    intel_text = _build_market_overview_summary(overview)
    time_prefix = f"Current system time: {current_time}\n\n" if current_time else ""
    ctx_lines = [f"User asked: {user_input}"]
    if history_summary:
        ctx_lines.append(f"Conversation context: {history_summary}")
    if strategy:
        ctx_lines.append(f"Orchestrator reasoning: {strategy}")
    ctx_prefix = "\n".join(ctx_lines) + "\n\n"
    messages = [
        {"role": "system", "content": MARKET_INTELLIGENCE_SYSTEM},
        {"role": "user", "content": ctx_prefix + time_prefix + intel_text},
    ]
    return await deepseek.chat_json(messages, temperature=0.1, max_tokens=1024)
