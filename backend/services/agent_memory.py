"""Agent Memory System - 持久化缓存，按 agent 分类，支持 TTL 过期"""
import asyncio
import hashlib
import json
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# TTL 规则（分钟）
TTL_RULES = {
    "quant_researcher":       {"trading": 5,  "non_trading": 60},
    "market_intelligence":    {"trading": 5,  "non_trading": 60},
    "trade_executor":         {"trading": 15, "non_trading": 15},
    "portfolio_monitor":      {"trading": 30, "non_trading": 30},
    "chief_strategist":       {"trading": 10, "non_trading": 10},
}


def _compute_ttl_minutes(agent_key: str) -> int:
    """根据 agent 类型和当前交易状态计算 TTL（分钟）"""
    from .trading_time import TradingTimeChecker
    rule = TTL_RULES.get(agent_key, {"trading": 15, "non_trading": 60})
    if TradingTimeChecker.is_trading_time():
        return rule["trading"]
    else:
        return rule["non_trading"]


def compute_query_hash(user_input: str, agent_key: str, symbol: str = "") -> str:
    """计算查询哈希，用于检测重复查询"""
    raw = f"{user_input}|{agent_key}|{symbol or 'none'}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _embed_query_async(query: str) -> str | None:
    """在后台线程向量化 query，返回 JSON 字符串；不可用时返回 None。"""
    if not query:
        return None
    try:
        from .embedding import embed_one
        vec = await asyncio.to_thread(embed_one, query)
        if vec:
            return json.dumps(vec)
    except Exception:
        pass
    return None


async def save_agent_memory(user_id: str, agent_key: str, symbol: str,
                            query_hash: str, result: str,
                            query: str = None) -> None:
    """保存 agent 记忆到 DB（附带原始 query 文本与向量，供语义检索）。

    L4 跨会话去重：写入前先按语义相似度查找既有有效记忆，命中则刷新旧条目，
    避免同一主题反复产生重复记忆、污染长期记忆库。
    """
    from . import db
    ttl_minutes = _compute_ttl_minutes(agent_key)
    expires_at = (datetime.now() + timedelta(minutes=ttl_minutes)).strftime("%Y-%m-%d %H:%M:%S")
    embedding = await _embed_query_async(query)

    if query and embedding:
        dup_id = await _find_near_duplicate(user_id, agent_key, symbol, embedding)
        if dup_id is not None:
            try:
                await db.refresh_agent_memory(dup_id, result, expires_at, query=query, embedding=embedding)
                return
            except Exception as e:
                logger.warning(f"刷新重复 agent_memory 失败 ({agent_key}): {e}")

    try:
        await db.save_agent_memory(user_id, agent_key, symbol, query_hash, result, expires_at,
                                   query=query, embedding=embedding)
    except Exception as e:
        logger.warning(f"保存 agent_memory 失败 ({agent_key}): {e}")


async def _find_near_duplicate(user_id: str, agent_key: str, symbol: str,
                               embedding: str) -> int | None:
    """在既有有效记忆中查找语义近似条目，返回其 id；无命中返回 None。"""
    from . import db
    from .rag_service import _parse_vector, cosine_similarity
    from ..config import AGENT_MEMORY_DEDUP_SCORE

    try:
        qvec = json.loads(embedding) if isinstance(embedding, str) else embedding
    except (json.JSONDecodeError, TypeError):
        return None

    try:
        candidates = await db.list_agent_memory(user_id, agent_key, symbol, limit=50)
    except Exception:
        return None

    for c in candidates:
        cvec = _parse_vector(c.get("embedding"))
        if cvec is None:
            continue
        try:
            score = cosine_similarity(qvec, cvec)
        except Exception:
            continue
        if score >= AGENT_MEMORY_DEDUP_SCORE:
            return c["id"]
    return None


async def get_agent_memory(user_id: str, agent_key: str,
                           symbol: str = None,
                           query_hash: str = None,
                           query: str = None) -> str | None:
    """两级查询 agent 内存缓存。

    L1：哈希精确匹配（现状，0ms）
    L2：语义向量检索（字符 n-gram 相似度）——换一种问法也能命中
    命中且未过期返回 cached result；否则返回 None。
    """
    from . import db
    try:
        # L1：哈希精确匹配
        if query_hash:
            row = await db.get_agent_memory(user_id, agent_key, query_hash)
            if row:
                if _is_expired(row.get("expires_at", "")):
                    return None
                return row.get("result")

        # L2：语义检索（向量嵌入 + 余弦相似度，降级 n-gram）——换一种问法也能命中
        if query:
            candidates = await db.list_agent_memory(user_id, agent_key, symbol)
            docs = [
                {"text": c.get("query") or "", "result": c.get("result"),
                 "embedding": c.get("embedding")}
                for c in candidates
                if c.get("query")
            ]
            if docs:
                from .rag_service import search
                hits = await asyncio.to_thread(search, query, docs, top_k=1)
                if hits:
                    return _qualify_memory_hit(hits[0])
    except Exception as e:
        logger.warning(f"查询 agent_memory 失败 ({agent_key}): {e}")
    return None


def _qualify_memory_hit(hit: dict):
    """对语义检索命中做质量门槛与长度预算，避免低质量/超长缓存直接注入上下文。

    - 命中分数低于阈值（或无分数）→ 放弃缓存，返回 None 触发实时 LLM。
    - 结果超长且为可解析 JSON → 放弃（避免截断破坏结构）；纯文本超长 → 截断到预算。
    """
    from ..config import AGENT_MEMORY_MIN_SCORE, AGENT_MEMORY_MAX_RESULT_CHARS

    score = hit.get("_score")
    if score is None:
        return None
    try:
        if float(score) < AGENT_MEMORY_MIN_SCORE:
            return None
    except (TypeError, ValueError):
        return None

    result = hit.get("result") or ""
    if len(result) <= AGENT_MEMORY_MAX_RESULT_CHARS:
        return result
    try:
        json.loads(result)
        return None
    except (ValueError, TypeError):
        return result[:AGENT_MEMORY_MAX_RESULT_CHARS]


def _is_expired(expires_at_str: str) -> bool:
    """判断缓存是否过期；无法解析时视为未过期（交由 DB 层判定）。"""
    if not expires_at_str:
        return False
    try:
        expires_at = datetime.strptime(expires_at_str, "%Y-%m-%d %H:%M:%S")
        return expires_at < datetime.now()
    except ValueError:
        return False
