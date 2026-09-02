"""系统设置 API - 读取/更新 .env 配置"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..middleware.error_handler import get_current_user
from ..services import db
from ..services.trading_time import CHINA_TZ

logger = logging.getLogger(__name__)

router = APIRouter(tags=["settings"])


def _bjt_today() -> str:
    """北京时间日期（Y-m-d）：初始资金/风控的"每日"限制以北京日为界"""
    return datetime.now(CHINA_TZ).date().isoformat()


# 项目根目录 & .env 路径
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ENV_PATH = _PROJECT_ROOT / ".env"
_DATA_DIR = _PROJECT_ROOT / "data"
_BALANCE_CHANGE_FILE = _DATA_DIR / "last_balance_change.json"

# 设置项定义
# (key, category, label, description, type)
# type: "string" | "int" | "float"
_SETTING_DEFS: list[tuple[str, str, str, str, str]] = [
    (
        "DEEPSEEK_API_KEY",
        "模型配置",
        "API密钥",
        "API密钥 - Flash模型用于对话分类等轻量任务，Pro模型用于技术分析/报告等深度推理（两个模型共用同一API Key）",
        "string",
    ),
    ("DEEPSEEK_BASE_URL", "模型配置", "API接口地址", "API接口地址", "string"),
    (
        "DEEPSEEK_FLASH_MODEL",
        "模型配置",
        "Flash模型",
        "Flash模型 - 用于意图识别、对话、文本结构化等快速响应任务",
        "string",
    ),
    (
        "DEEPSEEK_PRO_MODEL",
        "模型配置",
        "Pro模型",
        "Pro模型 - 用于技术分析、交易计划、风险研判等深度推理任务",
        "string",
    ),
    (
        "THINKING_MODE",
        "模型配置",
        "思考模式",
        "思考模式 - auto=按场景自动路由 / fast=全部走Flash(最快) / deep=深度分析强制Pro",
        "string",
    ),
    (
        "LIVE_PRICE_POLL_INTERVAL",
        "行情与限流",
        "行情轮询间隔",
        "行情轮询间隔(秒) - 1s=近实时",
        "int",
    ),
    ("API_RATE_LIMIT", "行情与限流", "API限流", "API限流(次/窗口)", "int"),
    ("API_RATE_WINDOW", "行情与限流", "限流窗口", "限流窗口(秒)", "int"),
    ("DB_PATH", "数据存储", "数据存储路径", "数据存储路径", "string"),
    (
        "INITIAL_BALANCE",
        "交易模拟",
        "初始资金",
        "初始资金 - 每日限修改一次，不可低于当前持仓总市值",
        "float",
    ),
    ("HTTP_TIMEOUT_STOCK", "超时设置", "行情API超时", "行情API超时(秒)", "int"),
    ("HTTP_TIMEOUT_LLM_CHAT", "超时设置", "LLM对话超时", "LLM对话超时(秒)", "int"),
    ("HTTP_TIMEOUT_LLM_STREAM", "超时设置", "LLM流式超时", "LLM流式超时(秒)", "int"),
    ("MAX_RECENT_MESSAGES", "对话记忆", "最大近期消息数", "最大近期消息数", "int"),
    ("MAX_TOTAL_MESSAGES", "对话记忆", "最大总消息数", "最大总消息数", "int"),
    ("SUMMARY_TRIM_THRESHOLD", "对话记忆", "摘要裁剪阈值", "摘要裁剪阈值", "int"),
]


def _mask_api_key(key: str) -> str:
    """对 API Key 做脱敏：保留首4位和末4位，中间用 *** 代替"""
    if not key or len(key) < 8:
        return "***"
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


def _read_env() -> dict[str, str]:
    """解析 .env 文件为 {KEY: VALUE} 字典"""
    result: dict[str, str] = {}
    if not _ENV_PATH.exists():
        return result
    with open(_ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            result[key] = value
    return result


def _write_env(updates: dict[str, str]) -> None:
    """将 updates 合并写入 .env 文件（保留原有注释和空行尽可能）"""
    if not _ENV_PATH.exists():
        # 如果 .env 不存在，从模板创建
        raise HTTPException(status_code=500, detail=".env 文件不存在")

    lines: list[str] = []
    with open(_ENV_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()

    updated_keys: set[str] = set()
    new_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line.rstrip("\n"))
            continue
        key, _, _ = stripped.partition("=")
        key = key.strip()
        if key in updates:
            new_lines.append(f"{key}={updates[key]}")
            updated_keys.add(key)
        else:
            new_lines.append(line.rstrip("\n"))

    # 追加未在 .env 中出现的 key
    for key, value in updates.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={value}")

    with open(_ENV_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(new_lines) + "\n")


def _reload_env() -> None:
    """重新加载 .env 到 os.environ"""
    try:
        from dotenv import load_dotenv

        load_dotenv(_ENV_PATH, override=True)
    except ImportError:
        logger.debug("dotenv not installed")


def _read_last_balance_change() -> str | None:
    """读取上次修改初始资金的日期"""
    if not _BALANCE_CHANGE_FILE.exists():
        return None
    try:
        data = json.loads(_BALANCE_CHANGE_FILE.read_text(encoding="utf-8"))
        return data.get("last_change_date")
    except (json.JSONDecodeError, KeyError):
        return None


def _write_last_balance_change(change_date: str) -> None:
    """记录本次修改初始资金的日期"""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _BALANCE_CHANGE_FILE.write_text(
        json.dumps({"last_change_date": change_date}, ensure_ascii=False),
        encoding="utf-8",
    )


async def _get_current_total_market_value() -> float:
    """获取当前持仓总市值（所有持仓 * 当前价的总和）"""
    try:
        from ..services.live_prices import get_live_price_batch
        from ..services.position_service import get_positions

        positions = await get_positions(user_id="default")
        if not positions:
            return 0.0

        symbols = [p.get("symbol", "") for p in positions if p.get("symbol")]
        if not symbols:
            return 0.0

        prices = get_live_price_batch(symbols)
        total = 0.0
        for p in positions:
            symbol = p.get("symbol", "")
            qty = float(p.get("quantity", 0))
            price = prices.get(symbol, 0.0)
            total += qty * price
        return total
    except Exception:
        logger.warning("获取持仓总市值失败，使用0作为默认值", exc_info=True)
        return 0.0


# Pydantic Models
class SettingsUpdateRequest(BaseModel):
    settings: dict[str, Any] = Field(
        ...,
        description='要更新的设置键值对，如 {"DEEPSEEK_API_KEY": "sk-xxx"}',
        example={
            "DEEPSEEK_FLASH_MODEL": "deepseek-v4-flash",
            "LIVE_PRICE_POLL_INTERVAL": 2,
        },
    )


# Endpoints


@router.post("")
async def get_settings(user_id: str = Depends(get_current_user)):
    """获取当前系统设置（分类返回，API Key 脱敏展示）"""
    env = _read_env()

    # 按类别分组
    categorized: dict[str, list] = {}
    for key, category, label, description, val_type in _SETTING_DEFS:
        value = env.get(key, "")
        display_value = value

        # API Key 脱敏
        if key == "DEEPSEEK_API_KEY" and value:
            display_value = _mask_api_key(value)

        item = {
            "key": key,
            "label": label,
            "description": description,
            "type": val_type,
            "value": display_value,
            "is_masked": key == "DEEPSEEK_API_KEY" and bool(value),
        }

        categorized.setdefault(category, []).append(item)

    # 读取初始资金日限信息
    last_change = _read_last_balance_change()
    balance_blocked = last_change == _bjt_today()

    return {
        "categories": categorized,
        "meta": {
            "initial_balance_blocked": balance_blocked,
            "last_balance_change": last_change,
        },
    }


@router.put("")
async def update_settings(
    req: SettingsUpdateRequest, user_id: str = Depends(get_current_user)
):
    """批量更新系统设置，写入 .env 并热加载"""
    updates = req.settings
    if not updates:
        raise HTTPException(status_code=400, detail="settings 不能为空")

    # 构建有效 key 集合
    valid_keys = {d[0] for d in _SETTING_DEFS}
    invalid_keys = set(updates.keys()) - valid_keys
    if invalid_keys:
        raise HTTPException(
            status_code=400,
            detail=f"未知的设置项: {', '.join(sorted(invalid_keys))}",
        )

    to_write: dict[str, str] = {}

    for key, value in updates.items():
        # 跳过脱敏值
        if (
            key == "DEEPSEEK_API_KEY"
            and isinstance(value, str)
            and value.startswith("***")
        ):
            continue

        # INITIAL_BALANCE 日限检查
        if key == "INITIAL_BALANCE":
            today_str = _bjt_today()
            last_change = _read_last_balance_change()
            if last_change == today_str:
                raise HTTPException(
                    status_code=400, detail="初始资金每日限修改一次，今日已修改过"
                )

            # 检查是否低于当前持仓总市值
            try:
                new_balance = float(value)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="初始资金必须是数字")
            if new_balance <= 0:
                raise HTTPException(status_code=400, detail="初始资金必须大于0")

            current_market_value = await _get_current_total_market_value()
            if new_balance < current_market_value:
                raise HTTPException(
                    status_code=400,
                    detail=f"初始资金({new_balance:.2f})不能低于当前持仓总市值({current_market_value:.2f})",
                )

            _write_last_balance_change(today_str)

        # 类型校验
        def_info = next((d for d in _SETTING_DEFS if d[0] == key), None)
        if def_info:
            val_type = def_info[4]
            if val_type == "int":
                try:
                    int(value)
                except (TypeError, ValueError):
                    raise HTTPException(status_code=400, detail=f"{key} 必须是整数")
            elif val_type == "float":
                try:
                    float(value)
                except (TypeError, ValueError):
                    raise HTTPException(status_code=400, detail=f"{key} 必须是数字")

        to_write[key] = str(value)

    if to_write:
        _write_env(to_write)
        _reload_env()
        # 初始资金变更时，同步重置模拟账户余额（总资产 = 新初始资金 + 持仓市值）
        if "INITIAL_BALANCE" in to_write:
            new_balance = float(to_write["INITIAL_BALANCE"])
            market_value = await _get_current_total_market_value()
            await db.update_account_balance(
                "default", new_balance, new_balance + market_value
            )
        logger.info(f"设置已更新: {', '.join(to_write.keys())}")

    return {"success": True, "updated_keys": list(to_write.keys())}
