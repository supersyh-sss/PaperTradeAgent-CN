"""Headline Arena API — 第三方预测竞技场的状态、成绩单与手动触发"""

import logging

from fastapi import APIRouter, Depends

from .. import config
from ..middleware.error_handler import get_current_user
from ..services import headline_arena_client as hac
from ..services.arena_daily import run_arena_daily

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/arena", tags=["arena"])


@router.get("/status")
async def arena_status(_: str = Depends(get_current_user)):
    """Arena 接入状态：是否启用、凭据是否齐全、开放题目概况。"""
    agent_id, client_secret = hac._credentials()
    status = {
        "enabled": config.HEADLINE_ARENA_ENABLED,
        "configured": bool(agent_id and client_secret),
        "agent_id": agent_id or None,
        "daily_assets": config.HEADLINE_ARENA_DAILY_ASSETS,
        "check_interval": config.HEADLINE_ARENA_CHECK_INTERVAL,
        "challenges": {"total_open": 0, "matching_assets": 0},
    }
    if not (config.HEADLINE_ARENA_ENABLED and status["configured"]):
        return status
    try:
        challenges = await hac.get_open_challenges()
        status["challenges"]["total_open"] = len(challenges)
        status["challenges"]["matching_assets"] = sum(
            1
            for c in challenges
            if str(c.get("asset", "")).upper() in config.HEADLINE_ARENA_DAILY_ASSETS
        )
    except Exception as e:
        logger.warning("Arena 状态查询开放题目失败: %s", e)
    return status


@router.get("/scorecard")
async def arena_scorecard(_: str = Depends(get_current_user)):
    """名片 + 成绩单 + 校准数据聚合（未启用/未配置时返回 enabled=False）。"""
    if not config.HEADLINE_ARENA_ENABLED:
        return {"enabled": False}
    card = await hac.get_agent_card()
    scorecard = await hac.get_scorecard()
    calibration = await hac.get_calibration()
    return {
        "enabled": True,
        "card": card,
        "scorecard": scorecard,
        "calibration": calibration,
    }


@router.get("/predictions")
async def arena_predictions(_: str = Depends(get_current_user)):
    """本 agent 的预测历史（未启用时返回空列表）。"""
    if not config.HEADLINE_ARENA_ENABLED:
        return {"enabled": False, "predictions": []}
    predictions = await hac.get_my_predictions()
    return {"enabled": True, "total": len(predictions), "predictions": predictions}


@router.post("/run-now")
async def arena_run_now(_: str = Depends(get_current_user)):
    """手动触发一轮每日预测（幂等，已提交的题目会跳过）。"""
    if not config.HEADLINE_ARENA_ENABLED:
        return {"success": False, "message": "Headline Arena 未启用（HEADLINE_ARENA_ENABLED）"}
    try:
        result = await run_arena_daily("manual")
        return {"success": True, "result": result}
    except Exception as e:
        logger.warning("Arena 手动触发失败: %s", e)
        return {"success": False, "message": str(e)[:200]}
