"""语义检索服务 — 向量嵌入 + 余弦相似度（fastembed + numpy），降级 n-gram

默认使用 embedding 模型（BAAI/bge-small-zh-v1.5）做真正的语义检索；
当 embedding 不可用时，降级到字符 n-gram + Dice 系数兜底，保证可离线、可单测。
"""
import json
import logging
import re
from collections.abc import Iterable

import numpy as np

from ..config import EMBEDDING_SIMILARITY_THRESHOLD

logger = logging.getLogger(__name__)

# 文档携带预计算向量的字段名（JSON 字符串或 list）
VECTOR_KEY = "embedding"

# n-gram 兜底阈值（Dice 系数，与余弦阈值口径不同）
_NGRAM_FALLBACK_THRESHOLD = 0.4


# ---- 向量相似度 ----
def cosine_similarity(a: list[float], b: list[float]) -> float:
    va = np.asarray(a, dtype=np.float32)
    vb = np.asarray(b, dtype=np.float32)
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def embed_query(query: str) -> list[float] | None:
    from .embedding import embed_one
    return embed_one(query)


def embed_texts(texts: list[str]) -> list[list[float]] | None:
    from .embedding import embed
    return embed(texts)


# ---- n-gram 兜底 ----
def _ngrams(text: str, n: int = 2) -> set:
    cleaned = re.sub(r"\s+", "", (text or "").lower())
    if not cleaned:
        return set()
    if len(cleaned) <= n:
        return {cleaned}
    return {cleaned[i:i + n] for i in range(len(cleaned) - n + 1)}


def ngram_similarity(a: str, b: str) -> float:
    """字符 n-gram Dice 相似度（embedding 不可用时的降级口径）。"""
    sa, sb = _ngrams(a), _ngrams(b)
    if not sa or not sb:
        return 0.0
    return 2 * len(sa & sb) / (len(sa) + len(sb))


def _ngram_search(query: str, documents: list[dict], top_k: int) -> list:
    scored = []
    for doc in documents:
        text = doc.get("text", "")
        score = ngram_similarity(query, text)
        if score >= _NGRAM_FALLBACK_THRESHOLD:
            item = dict(doc)
            item["_score"] = round(score, 4)
            scored.append(item)
    scored.sort(key=lambda d: d["_score"], reverse=True)
    return scored[:top_k]


def _parse_vector(value) -> list[float] | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(value, (list, tuple)):
        return None
    try:
        return [float(x) for x in value]
    except (TypeError, ValueError):
        return None


# ---- 统一检索入口 ----
def search(query: str, documents: Iterable[dict], *, top_k: int = 3,
           threshold: float | None = None,
           query_vector: list[float] | None = None) -> list:
    """向量语义检索（embedding + 余弦），embedding 不可用时降级 n-gram。

    Args:
        query: 查询文本
        documents: 文档字典列表；每项可含 "text"（原文，供降级）与
                   "embedding"（预计算向量，JSON 字符串或 list）
        top_k: 最多返回条数
        threshold: 余弦相似度阈值；默认取 config 的 EMBEDDING_SIMILARITY_THRESHOLD
        query_vector: 可选的预计算查询向量；提供时跳过在线嵌入（供测试/复用）

    Returns:
        按相似度降序的文档列表（附加 "_score" 字段，0~1）
    """
    if threshold is None:
        threshold = EMBEDDING_SIMILARITY_THRESHOLD

    docs = [dict(d) if isinstance(d, dict) else {"text": str(d)} for d in documents]
    if not docs:
        return []

    qvec = query_vector if query_vector is not None else embed_query(query)
    if qvec is None:
        # embedding 不可用 → 降级 n-gram
        return _ngram_search(query, docs, top_k)

    # embedding 可用 → 仅对带向量的文档做余弦检索，避免与 n-gram 混合
    scored = []
    for doc in docs:
        dvec = _parse_vector(doc.get(VECTOR_KEY))
        if dvec is None:
            continue
        score = cosine_similarity(qvec, dvec)
        if score >= threshold:
            item = dict(doc)
            item["_score"] = round(score, 4)
            scored.append(item)
    scored.sort(key=lambda d: d["_score"], reverse=True)
    return scored[:top_k]
