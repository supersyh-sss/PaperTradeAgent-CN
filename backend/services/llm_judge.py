"""LLM-as-judge 评估器（L5 可观测性评估 — 离线，需 DeepSeek API）

用 Flash 模型作为「评判员」，对两类对象做结构化评分：
  1. 意图分类：给定用户输入，输出意图 + 置信度
  2. 输出质量：给定（问题, 回答, 参考要点），从相关性/完整性/事实性/格式合规四维打分

与确定性 eval（tests/eval_intent.py）互补：
  - eval_intent.py 进 CI 门禁（无 API 依赖、确定性）
  - 本模块做离线质量回归（依赖 API、有 token 成本、结果带随机性，不进入 CI）
"""
import logging
from typing import Dict, List, Optional

from .llm import flash_client

logger = logging.getLogger(__name__)

INTENT_VALUES = ["analyze", "trade", "query", "portfolio", "watchlist", "chat", "market", "cancel_order"]

_INTENT_LABELS = {
    "analyze": "分析", "trade": "交易", "query": "查询", "portfolio": "持仓",
    "watchlist": "自选股", "chat": "对话", "market": "市场概览", "cancel_order": "撤单",
}


def build_intent_messages(user_input: str) -> list:
    """构造意图分类评判的 messages（deterministic，便于单测）。"""
    labels = " / ".join(f"{k}({v})" for k, v in _INTENT_LABELS.items())
    system = (
        "你是金融交易助手的意图分类评审员。只根据用户输入判断最贴切的单一意图，"
        f"从以下枚举中选择：{labels}。"
        '输出 JSON：{"intent": "<枚举值>", "confidence": <0~1 浮点数>}'
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_input},
    ]


def build_quality_messages(question: str, answer: str,
                           reference_points: Optional[List[str]] = None) -> list:
    """构造输出质量评判的 messages（deterministic，便于单测）。"""
    ref_text = "\n".join(f"- {p}" for p in (reference_points or [])) if reference_points else "（未提供）"
    system = (
        "你是金融助手的回答质量评审员。基于用户问题与参考要点，对回答打分。\n"
        "四个维度各 0~5 分：relevance（是否切题）、completeness（是否完整覆盖要点）、"
        "factuality（是否无幻觉/无编造数据）、format_ok（是否结构清晰，布尔）。\n"
        '输出 JSON：{"relevance":int,"completeness":int,"factuality":int,'
        '"format_ok":bool,"overall":int,"reason":"str","issues":["str"]}\n'
        "overall 为 0~5 综合分，issues 列出具体问题（无则空数组）。"
    )
    user = (
        f"用户问题：{question}\n\n"
        f"参考要点：\n{ref_text}\n\n"
        f"待评审回答：\n{answer}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


async def judge_intent(user_input: str) -> Dict:
    """用 Flash 模型对用户输入做意图分类，返回 {intent, confidence}（含容错）。"""
    try:
        result = await flash_client.chat_json(
            build_intent_messages(user_input), temperature=0.0, max_tokens=256
        )
    except Exception as e:
        logger.warning("意图评判调用失败：%s", e)
        return {"intent": "", "confidence": 0.0, "error": str(e)}

    if result.get("parse_error"):
        return {"intent": "", "confidence": 0.0, "raw": result.get("raw", "")}

    intent = result.get("intent", "")
    if intent not in INTENT_VALUES:
        return {"intent": "", "confidence": 0.0, "raw": result}
    try:
        confidence = float(result.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return {"intent": intent, "confidence": confidence}


async def judge_answer_quality(question: str, answer: str,
                               reference_points: Optional[List[str]] = None) -> Dict:
    """用 Flash 模型评估回答质量，返回结构化评分。"""
    try:
        result = await flash_client.chat_json(
            build_quality_messages(question, answer, reference_points),
            temperature=0.0, max_tokens=512,
        )
    except Exception as e:
        logger.warning("质量评判调用失败：%s", e)
        return {"overall": 0, "error": str(e)}

    if result.get("parse_error"):
        return {"overall": 0, "raw": result.get("raw", "")}

    out = {}
    for k in ("relevance", "completeness", "factuality", "overall"):
        try:
            out[k] = int(result.get(k, 0))
        except (TypeError, ValueError):
            out[k] = 0
    out["format_ok"] = bool(result.get("format_ok", False))
    out["reason"] = str(result.get("reason", ""))
    issues = result.get("issues", [])
    out["issues"] = issues if isinstance(issues, list) else []
    return out
