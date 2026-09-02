"""L4 语义检索（rag_service）单元测试 — 向量余弦 + n-gram 兜底，确定性可重复。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.services.rag_service import cosine_similarity, ngram_similarity, search


# ---- n-gram 兜底相似度（确定性） ----
def test_ngram_identical():
    assert ngram_similarity("分析一下茅台", "分析一下茅台") == 1.0


def test_ngram_related_scores_higher_than_unrelated():
    related = ngram_similarity("分析一下茅台", "帮我分析茅台")
    unrelated = ngram_similarity("分析一下茅台", "看看平安银行怎么样")
    assert related > unrelated
    assert related >= 0.35
    assert unrelated < 0.35


def test_ngram_unrelated_is_low():
    assert ngram_similarity("查看持仓", "今天天气怎么样") < 0.35


# ---- 向量余弦相似度（确定性） ----
def test_cosine_identical():
    assert abs(cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) - 1.0) < 1e-6


def test_cosine_orthogonal():
    assert abs(cosine_similarity([1.0, 0.0, 0.0], [0.0, 1.0, 0.0])) < 1e-6


# ---- search：注入 query_vector 做确定性向量检索 ----
def test_search_returns_ranked_hits_with_vectors():
    docs = [
        {"text": "分析一下茅台", "result": "r1", "embedding": [1.0, 0.0, 0.0]},
        {"text": "看看平安银行怎么样", "result": "r2", "embedding": [0.0, 1.0, 0.0]},
        {"text": "帮我分析茅台", "result": "r3", "embedding": [0.9, 0.1, 0.0]},
    ]
    hits = search("x", docs, top_k=2, threshold=0.5, query_vector=[1.0, 0.0, 0.0])
    assert hits, "应命中至少一条相似文档"
    assert hits[0]["result"] == "r1"
    assert hits[0]["_score"] >= hits[-1]["_score"]


def test_search_filters_below_threshold_with_vectors():
    docs = [{"text": "x", "result": "x", "embedding": [0.0, 1.0, 0.0]}]
    hits = search("q", docs, threshold=0.9, query_vector=[1.0, 0.0, 0.0])
    assert hits == []


def test_search_empty_documents():
    assert search("任意查询", [], threshold=0.0) == []
