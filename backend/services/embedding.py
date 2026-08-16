"""Embedding 服务 — 基于 fastembed 的本地向量化（ONNX，无需 torch）

用主流 embedding 模型（默认 BAAI/bge-small-zh-v1.5）把文本转为向量，
替代此前的字符 n-gram 相似度，实现真正的语义检索。

设计要点：
  - 懒加载单例：首次调用才下载/加载模型，避免拖慢启动
  - 国内网络走 HF_ENDPOINT 镜像（hf-mirror.com）
  - 任何异常都返回 None，由上层降级到 n-gram，保证系统不因嵌入不可用而崩溃
"""
import logging
import os
from pathlib import Path
from typing import List, Optional

from ..config import EMBEDDING_MODEL, EMBEDDING_DEVICE

logger = logging.getLogger(__name__)

_embedder = None
_embedder_failed = False

# 模型缓存目录：项目内持久化，避免默认落在系统 Temp 导致重复下载
_CACHE_DIR = str(Path(__file__).resolve().parent.parent / "cache" / "fastembed")


def _ensure_hf_mirror() -> None:
    """国内环境优先使用 HuggingFace 镜像下载模型。"""
    if not os.environ.get("HF_ENDPOINT"):
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"


def get_embedder():
    """返回已加载的 TextEmbedding 实例；不可用时返回 None。"""
    global _embedder, _embedder_failed
    if _embedder is not None:
        return _embedder
    if _embedder_failed:
        return None
    try:
        _ensure_hf_mirror()
        os.makedirs(_CACHE_DIR, exist_ok=True)
        from fastembed import TextEmbedding
        _embedder = TextEmbedding(
            model_name=EMBEDDING_MODEL,
            cache_dir=_CACHE_DIR,
            cuda=(EMBEDDING_DEVICE not in ("cpu", "CPU")),
        )
        # 预热加载，尽早暴露下载/推理失败
        list(_embedder.embed(["预热"]))
        logger.info("Embedding 模型已加载：%s (device=%s)", EMBEDDING_MODEL, EMBEDDING_DEVICE)
        return _embedder
    except Exception as e:
        _embedder_failed = True
        logger.warning("Embedding 模型不可用，语义检索将降级为 n-gram：%s", e)
        return None


def embed(texts: List[str]) -> Optional[List[List[float]]]:
    """批量向量化文本；失败返回 None。

    Args:
        texts: 文本列表

    Returns:
        与输入等长的浮点向量列表，或 None（嵌入不可用/失败）
    """
    if not texts:
        return []
    embedder = get_embedder()
    if embedder is None:
        return None
    try:
        vectors = list(embedder.embed([(t or "") for t in texts]))
        return [v.tolist() for v in vectors]
    except Exception as e:
        logger.warning("Embedding 推理失败：%s", e)
        return None


def embed_one(text: str) -> Optional[List[float]]:
    """向量化单条文本。"""
    res = embed([text])
    if res:
        return res[0]
    return None
