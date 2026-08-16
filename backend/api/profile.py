"""用户画像 API — 首次使用引导页配置

提供：
  - 画像读写（昵称 / 头像 / 风险偏好 / 引导完成状态）
  - 风险评估问卷（确定性打分，无 LLM 依赖）
  - 风险得分 → 风险等级映射

设计说明：引导页可跳过；被跳过的字段由前端填默认值后再落库。
画像数据会被下游 Agent（策略/风控）作为用户风险偏好参考。
"""
import logging
from typing import Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..middleware.error_handler import get_current_user
from ..services import db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/profile", tags=["profile"])

# 风险评估问卷（10 题，覆盖投资期限/收益预期/回撤承受/经验/操作纪律/资产占比等维度）
RISK_QUESTIONS = [
    {
        "id": "time_horizon",
        "question": "这笔资金你计划投资多久？",
        "options": [
            {"label": "1 年以内", "score": 0},
            {"label": "1 - 3 年", "score": 1},
            {"label": "3 - 5 年", "score": 2},
            {"label": "5 年以上", "score": 3},
        ],
    },
    {
        "id": "return_expectation",
        "question": "你的年化收益预期是？",
        "options": [
            {"label": "跑赢存款/理财即可", "score": 0},
            {"label": "5% - 10%", "score": 1},
            {"label": "10% - 20%", "score": 2},
            {"label": "20% 以上", "score": 3},
        ],
    },
    {
        "id": "loss_tolerance",
        "question": "你能承受的最大浮亏比例是？",
        "options": [
            {"label": "5% 以内（保本优先）", "score": 0},
            {"label": "5% - 10%", "score": 1},
            {"label": "10% - 20%", "score": 2},
            {"label": "20% 以上（追求高收益）", "score": 3},
        ],
    },
    {
        "id": "experience",
        "question": "你的股票/基金投资经验？",
        "options": [
            {"label": "新手，刚开始接触", "score": 0},
            {"label": "1 - 3 年", "score": 1},
            {"label": "3 - 5 年", "score": 2},
            {"label": "5 年以上", "score": 3},
        ],
    },
    {
        "id": "drawdown_action",
        "question": "持仓出现较大浮亏时，你通常会？",
        "options": [
            {"label": "立即全部卖出", "score": 0},
            {"label": "部分减仓控制风险", "score": 1},
            {"label": "持有观望", "score": 2},
            {"label": "逢低加仓摊薄成本", "score": 3},
        ],
    },
    {
        "id": "product_knowledge",
        "question": "你对高波动品种（小盘股、题材股等）的认知？",
        "options": [
            {"label": "完全不了解", "score": 0},
            {"label": "了解，但基本不参与", "score": 1},
            {"label": "了解，并少量参与", "score": 2},
            {"label": "熟悉，经常参与", "score": 3},
        ],
    },
    {
        "id": "position_management",
        "question": "你的仓位管理习惯是？",
        "options": [
            {"label": "分散持有，从不重仓单一标的", "score": 0},
            {"label": "多数分散，偶尔重仓", "score": 1},
            {"label": "经常重仓单一标的", "score": 2},
            {"label": "习惯满仓单一标的", "score": 3},
        ],
    },
    {
        "id": "stop_loss_discipline",
        "question": "你会设置并执行止损吗？",
        "options": [
            {"label": "严格执行预设止损", "score": 0},
            {"label": "多数时候会止损", "score": 1},
            {"label": "偶尔止损", "score": 2},
            {"label": "从不设止损", "score": 3},
        ],
    },
    {
        "id": "income_dependency",
        "question": "这笔资金在你个人资产中的占比？",
        "options": [
            {"label": "几乎全部积蓄，不能亏损", "score": 0},
            {"label": "大部分积蓄", "score": 1},
            {"label": "以闲置资金为主", "score": 2},
            {"label": "纯闲钱，亏损不影响生活", "score": 3},
        ],
    },
    {
        "id": "liquidity_need",
        "question": "这笔资金的可投资期限与流动性需求？",
        "options": [
            {"label": "随时可能需要用钱", "score": 0},
            {"label": "半年内可能动用", "score": 1},
            {"label": "1 - 2 年内无需动用", "score": 2},
            {"label": "可长期闲置", "score": 3},
        ],
    },
]

_RISK_LABELS = {
    "conservative": "保守型",
    "balanced": "均衡型",
    "aggressive": "进取型",
}


def _score_to_level(score: int) -> str:
    if score <= 10:
        return "conservative"
    if score <= 20:
        return "balanced"
    return "aggressive"


class ProfileUpdateRequest(BaseModel):
    nickname: Optional[str] = None
    avatar: Optional[str] = None
    risk_level: Optional[str] = None
    risk_score: Optional[int] = None
    onboarding_completed: Optional[bool] = None


class RiskAssessmentRequest(BaseModel):
    answers: Dict[str, int] = Field(
        ..., description="题目 id → 选中选项下标，如 {'loss_tolerance': 1}"
    )


@router.get("")
async def get_profile(user_id: str = Depends(get_current_user)):
    """读取用户画像；不存在时按默认值自动初始化（昵称=投资者、头像=blue、均衡型）"""
    profile = await db.get_user_profile(user_id)
    if not profile:
        await db.save_user_profile(
            user_id, nickname="投资者", avatar="blue",
            risk_level="balanced", risk_score=0, onboarding_completed=1,
        )
        profile = await db.get_user_profile(user_id)
    return {"profile": profile, "onboarding_completed": bool(profile.get("onboarding_completed"))}


@router.put("")
async def update_profile(req: ProfileUpdateRequest, user_id: str = Depends(get_current_user)):
    """更新画像字段（只更新提供的字段，其余保留原值）"""
    existing = await db.get_user_profile(user_id) or {}

    nickname = req.nickname if req.nickname is not None else existing.get("nickname", "投资者")
    avatar = req.avatar if req.avatar is not None else existing.get("avatar", "blue")
    risk_level = req.risk_level if req.risk_level is not None else existing.get("risk_level", "balanced")
    risk_score = req.risk_score if req.risk_score is not None else existing.get("risk_score", 0)
    completed = req.onboarding_completed if req.onboarding_completed is not None else existing.get("onboarding_completed", 1)

    if risk_level not in _RISK_LABELS:
        risk_level = "balanced"

    await db.save_user_profile(
        user_id, nickname=nickname, avatar=avatar,
        risk_level=risk_level, risk_score=int(risk_score),
        onboarding_completed=int(bool(completed)),
    )
    return {"success": True}


@router.get("/risk-questions")
async def get_risk_questions(user_id: str = Depends(get_current_user)):
    """获取风险评估问卷"""
    return {"questions": RISK_QUESTIONS}


@router.post("/risk-assessment")
async def submit_risk_assessment(req: RiskAssessmentRequest, user_id: str = Depends(get_current_user)):
    """提交风险评估答案，返回风险得分与等级（确定性打分）"""
    total = 0
    for q in RISK_QUESTIONS:
        qid = q["id"]
        idx = req.answers.get(qid)
        if idx is None:
            continue
        options = q["options"]
        if isinstance(idx, int) and 0 <= idx < len(options):
            total += options[idx]["score"]

    level = _score_to_level(total)
    return {
        "risk_score": total,
        "risk_level": level,
        "risk_label": _RISK_LABELS[level],
    }
