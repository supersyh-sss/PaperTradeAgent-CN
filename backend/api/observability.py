"""可观测性与评估中心 API（L5/L6）

对外暴露系统运行的健康度画像：
  - 指标聚合：各 Agent 成功率 / 平均耗时 / Token 消耗 / 会话数
  - 链路追踪：每次 Agent 执行的 trace 明细（可回放与审计）
  - 确定性评估：意图识别安全网准确率（离线、可重复、无 LLM 依赖）
  - 审计事件：安全审计计数与近期事件

设计原则（对齐主流多 Agent 框架的可观测性要求）：
  - 全链路追踪（trace）持久化到 SQLite，接口只读聚合
  - 评估与运行分离：确定性评估进接口门禁，LLM-as-judge 走离线脚本
"""
import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from ..middleware.error_handler import get_current_user
from ..services import db, llm_judge

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/observability", tags=["observability"])

# 意图识别确定性评估数据集（与 tests/eval_intent.py 保持同源）
# (输入, 期望意图)
_INTENT_GOLDEN = [
    ("买入100股平安银行", "trade"),
    ("卖出200股茅台", "trade"),
    ("下单买入招商银行", "trade"),
    ("挂单卖出五粮液", "trade"),
    ("今天大盘怎么样", "market"),
    ("查看行情", "market"),
    ("市场走势如何", "market"),
    ("A股整体涨跌情况", "market"),
    ("白酒板块怎么样", "market"),
    ("新能源行业走势", "market"),
    ("查看持仓", "portfolio"),
    ("我的仓位怎么样", "portfolio"),
    ("盈亏情况如何", "portfolio"),
    ("我的账户情况", "portfolio"),
    ("添加自选", "watchlist"),
    ("删除自选", "watchlist"),
    ("移除自选", "watchlist"),
    ("帮我撤单", "cancel_order"),
    ("取消挂单", "cancel_order"),
    ("撤销委托订单", "cancel_order"),
    ("你好", "chat"),
    ("有点累", "chat"),
    ("谢谢", "chat"),
    ("今天天气不错", "chat"),
    ("你在吗", "chat"),
]


def _evaluate_intent() -> dict:
    """运行意图识别安全网的确定性评估（无 LLM / 网络依赖）。"""
    from ..agents.chief_strategist import _recover_from_chat

    correct = 0
    failures = []
    for text, expected in _INTENT_GOLDEN:
        got = _recover_from_chat(text)
        if got == expected:
            correct += 1
        else:
            failures.append({"input": text, "expected": expected, "got": got})

    return {
        "total": len(_INTENT_GOLDEN),
        "correct": correct,
        "accuracy": round(correct / len(_INTENT_GOLDEN), 4),
        "failures": failures,
        "mode": "deterministic_intent_safeguard",
    }


def _compute_alerts(metrics: dict, evaluation: dict) -> list:
    """基于运行指标与确定性评估生成简单的阈值告警标记（不推送、仅标记）。"""
    alerts = []

    avg_success = metrics.get("avg_success_rate")
    if avg_success is not None and avg_success < 0.9:
        alerts.append({
            "level": "warn",
            "metric": "avg_success_rate",
            "message": f"平均成功率 {avg_success:.1%} 低于 90%",
        })

    total_failed = metrics.get("total_failed") or 0
    if total_failed > 5:
        alerts.append({
            "level": "warn",
            "metric": "total_failed",
            "message": f"累计失败 {total_failed} 次",
        })

    for agent, m in (metrics.get("by_agent") or {}).items():
        total = m.get("total") or 0
        ok = m.get("ok") or 0
        if total and ok / total < 0.8:
            alerts.append({
                "level": "warn",
                "metric": f"agent:{agent}",
                "message": f"{agent} 成功率低于 80%",
            })

    acc = evaluation.get("accuracy")
    if acc is not None and acc < 0.9:
        alerts.append({
            "level": "warn",
            "metric": "intent_accuracy",
            "message": f"意图识别准确率 {acc:.1%} 低于 90%",
        })

    return alerts


@router.get("/overview")
async def observability_overview(user_id: str = Depends(get_current_user)):
    """可观测性总览：指标聚合 + 近期 trace + 确定性评估 + 审计计数。"""
    try:
        metrics = await db.get_metrics_summary()
    except Exception as e:
        logger.warning("聚合指标失败: %s", e)
        metrics = {}

    try:
        recent_traces = await db.get_agent_traces(limit=30)
    except Exception as e:
        logger.warning("读取 trace 失败: %s", e)
        recent_traces = []

    evaluation = _evaluate_intent()

    # 确定性评估结果落库，形成可追溯的历史趋势（失败不阻塞）
    try:
        await db.record_eval_result(
            eval_type="intent", mode="deterministic",
            total=evaluation["total"], correct=evaluation["correct"],
            accuracy=evaluation["accuracy"],
            detail=str(evaluation["failures"]) if evaluation["failures"] else "",
        )
    except Exception as e:
        logger.warning("意图评估落库失败: %s", e)

    audit = {"recent_events": 0}
    try:
        from ..harness.safety_gate import AuditLogger
        audit["recent_events"] = len(AuditLogger.get_recent(100))
    except Exception as e:
        logger.warning("读取审计日志失败: %s", e)

    return {
        "metrics": metrics,
        "recent_traces": recent_traces,
        "evaluation": {"intent": evaluation},
        "audit": audit,
        "alerts": _compute_alerts(metrics, evaluation),
    }


@router.get("/traces")
async def observability_traces(
    limit: int = Query(100, ge=1, le=500),
    agent: str | None = Query(None),
    user_id: str = Depends(get_current_user),
):
    """链路追踪明细列表（可按 agent 过滤）。"""
    try:
        traces = await db.get_agent_traces(agent=agent, limit=limit)
    except Exception as e:
        logger.warning("读取 trace 列表失败: %s", e)
        traces = []
    return {"total": len(traces), "traces": traces}


@router.get("/evaluations")
async def observability_evaluations(
    eval_type: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    user_id: str = Depends(get_current_user),
):
    """历史评估结果列表（确定性 eval 与 LLM-as-judge 均落库，按时间倒序）。"""
    try:
        results = await db.get_eval_results(eval_type=eval_type, limit=limit)
    except Exception as e:
        logger.warning("读取评估历史失败: %s", e)
        results = []
    return {"total": len(results), "evaluations": results}


class LLMJudgeRequest(BaseModel):
    """按需 LLM-as-judge 请求体。"""
    judge_type: str = "intent"  # intent | quality
    user_input: str | None = None
    question: str | None = None
    answer: str | None = None
    reference_points: list[str] | None = None


@router.post("/evaluations/llm-judge")
async def observability_llm_judge(
    req: LLMJudgeRequest,
    user_id: str = Depends(get_current_user),
):
    """按需运行 LLM-as-judge（依赖 DeepSeek Flash），并将结果落库。

    - intent：对确定性意图评估数据集跑一遍 LLM 意图分类，计算准确率
    - quality：对给定 (question, answer) 做四维质量评分
    """
    if req.judge_type == "quality":
        if not req.question or not req.answer:
            return {"ok": False, "error": "quality 评判需要 question 与 answer"}
        result = await llm_judge.judge_answer_quality(
            req.question, req.answer, req.reference_points
        )
        score = float(result.get("overall") or 0)
        await db.record_eval_result(
            eval_type="llm_judge", mode="llm_judge",
            total=1, correct=0, accuracy=0.0, score=score,
            detail=str(result),
        )
        return {"ok": True, "judge_type": "quality", "result": result}

    # 默认 intent：跑完整确定性数据集，对比期望意图
    correct = 0
    results = []
    for text, expected in _INTENT_GOLDEN:
        got = await llm_judge.judge_intent(text)
        is_correct = got.get("intent") == expected
        if is_correct:
            correct += 1
        results.append({"input": text, "expected": expected, **got, "correct": is_correct})

    total = len(_INTENT_GOLDEN)
    accuracy = round(correct / total, 4) if total else 0.0
    await db.record_eval_result(
        eval_type="llm_judge", mode="llm_judge",
        total=total, correct=correct, accuracy=accuracy,
        score=accuracy,
        detail=str([r for r in results if not r["correct"]]),
    )
    return {
        "ok": True,
        "judge_type": "intent",
        "total": total,
        "correct": correct,
        "accuracy": accuracy,
        "results": results,
    }
