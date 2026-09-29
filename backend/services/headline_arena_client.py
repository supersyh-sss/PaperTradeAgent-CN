"""Headline Arena 客户端 — 第三方 AI 预测竞技场（https://headlinearena.com）

职责：
  - client_credentials 换取 Bearer token（模块级缓存，过期前 60s 刷新）
  - 开放题目 / 市场上下文 / 历史预测 / 成绩单 / 校准曲线 的读取
  - 预测提交（probabilities 三键齐全且和为 1，confidence 取 argmax）

注意：统一携带自定义 User-Agent（平台曾对 /api/ 跑 Browser Integrity Check
拦截 Python 默认 UA，虽已对 API 路径放行，自定义 UA 仍是推荐做法）。
"""

import asyncio
import json
import logging
import time
from pathlib import Path

import httpx

from ..config import HEADLINE_ARENA_BASE_URL

logger = logging.getLogger(__name__)

_BASE_URL = HEADLINE_ARENA_BASE_URL.rstrip("/")
_API = f"{_BASE_URL}/api/v1"
_UA = "PaperTradeAgent/0.1 (+https://github.com/supersyh-sss/PaperTradeAgent-CN)"
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
# 边缘节点偶发 502/网络抖动时的重试间隔（GET 只读，重试安全）
_RETRY_DELAYS = (1.0, 2.5)
_RETRY_STATUSES = {502, 503, 504}

# 凭据兜底文件（相对项目根）：data/headlinearena/credentials.json
_CREDENTIALS_FILE = Path(__file__).resolve().parents[2] / "data" / "headlinearena" / "credentials.json"

# 模块级单例 httpx 客户端与 token 缓存
_client: httpx.AsyncClient | None = None
_token: str | None = None
_token_expires_at: float = 0.0


def _get_client() -> httpx.AsyncClient:
    """复用全局 AsyncClient，统一携带自定义 UA（否则被 Cloudflare 1010 拦截）。"""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=_TIMEOUT,
            headers={"User-Agent": _UA},
        )
    return _client


def _credentials() -> tuple[str, str]:
    """返回 (agent_id, client_secret)：优先环境变量，否则读凭据文件。"""
    from ..config import HEADLINE_ARENA_AGENT_ID, HEADLINE_ARENA_CLIENT_SECRET

    if HEADLINE_ARENA_AGENT_ID and HEADLINE_ARENA_CLIENT_SECRET:
        return HEADLINE_ARENA_AGENT_ID, HEADLINE_ARENA_CLIENT_SECRET

    try:
        data = json.loads(_CREDENTIALS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Headline Arena 凭据文件读取失败: %s", e)
        return "", ""

    # 兼容 ha_cli 存储结构：{origin: {"_agents": {id: {...}}, "_default_agent": id}}
    origin = data.get(_BASE_URL) or data.get("https://headlinearena.com") or {}
    agents = origin.get("_agents") or {}
    agent = agents.get(origin.get("_default_agent") or "")
    if not agent:
        # 兜底：任意一个含 agent_id+client_secret 的条目，或 origin 本身就是平铺结构
        for v in agents.values():
            if v.get("agent_id") and v.get("client_secret"):
                agent = v
                break
        agent = agent or (origin if origin.get("client_secret") else None)
    if not agent:
        logger.warning("Headline Arena 凭据文件中未找到有效 agent 条目")
        return "", ""
    return agent.get("agent_id", ""), agent.get("client_secret", "")


async def get_token(force: bool = False) -> str:
    """获取 Bearer token（模块级缓存，expires_in-60 秒提前刷新）。"""
    global _token, _token_expires_at
    if not force and _token and time.time() < _token_expires_at:
        return _token

    agent_id, client_secret = _credentials()
    if not agent_id or not client_secret:
        raise RuntimeError("Headline Arena 凭据缺失（环境变量或 credentials.json 均未提供）")

    try:
        resp = await _get_client().post(
            f"{_API}/agent/auth/token",
            json={
                "grant_type": "client_credentials",
                "agent_id": agent_id,
                "client_secret": client_secret,
            },
        )
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("Headline Arena 获取 token 失败: %s", e)
        raise RuntimeError(f"Headline Arena 获取 token 失败: {e}") from e

    _token = data.get("access_token") or ""
    if not _token:
        raise RuntimeError("Headline Arena token 响应缺少 access_token")
    expires_in = float(data.get("expires_in") or 3600)
    _token_expires_at = time.time() + max(expires_in - 60, 60)
    return _token


async def _request(method: str, path: str, *, json_body: dict | None = None) -> httpx.Response:
    """带 Bearer token 的统一请求入口。"""
    token = await get_token()
    return await _get_client().request(
        method,
        f"{_API}{path}",
        headers={"Authorization": f"Bearer {token}"},
        json=json_body,
    )


async def _get_with_retry(url: str, *, auth: bool = False) -> httpx.Response:
    """GET 只读请求：对边缘 5xx 与连接抖动做有限重试（共 3 次尝试）。"""
    attempts = len(_RETRY_DELAYS) + 1
    for i in range(attempts):
        headers = {"Authorization": f"Bearer {await get_token()}"} if auth else {}
        try:
            resp = await _get_client().get(url, headers=headers)
        except (httpx.TransportError, httpx.TimeoutException) as e:
            if i == attempts - 1:
                raise
            logger.info("Headline Arena GET 网络异常（%s），%.1fs 后重试", e, _RETRY_DELAYS[i])
            await asyncio.sleep(_RETRY_DELAYS[i])
            continue
        if resp.status_code in _RETRY_STATUSES and i < attempts - 1:
            logger.info(
                "Headline Arena GET 返回 %s，%.1fs 后重试（%s）",
                resp.status_code,
                _RETRY_DELAYS[i],
                url,
            )
            await asyncio.sleep(_RETRY_DELAYS[i])
            continue
        return resp
    raise RuntimeError("unreachable")


async def get_open_challenges() -> list[dict]:
    """开放题目列表（公开端点，带 token 也无妨）。"""
    try:
        resp = await _get_with_retry(f"{_API}/eval/challenges?status=open")
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items") if isinstance(data, dict) else data
        return items if isinstance(items, list) else []
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("Headline Arena 获取开放题目失败: %s", e)
        return []


async def get_market_context(asset: str) -> dict | None:
    """市场上下文（价格/指标/基线分布，公开端点）。

    注意：该端点对不支持的资产返回 200 + {"error": "context_unavailable"}，
    需显式归一为 None，不能靠真值判断。
    """
    try:
        resp = await _get_with_retry(f"{_API}/eval/context/{asset}")
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and data.get("error"):
            logger.info("Headline Arena %s 上下文不可用: %s", asset, data.get("error"))
            return None
        return data
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("Headline Arena 获取 %s 市场上下文失败: %s", asset, e)
        return None


async def get_my_predictions() -> list[dict]:
    """本 agent 的预测历史（响应结构做防御性解析）。"""
    agent_id, _ = _credentials()
    if not agent_id:
        return []
    try:
        resp = await _get_with_retry(f"{_API}/eval/agents/{agent_id}/predictions", auth=True)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items") if isinstance(data, dict) else data
        return items if isinstance(items, list) else []
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("Headline Arena 获取预测历史失败: %s", e)
        return []


async def submit_prediction(
    challenge_id: str,
    probabilities: dict,
    summary: str,
    reasoning: str,
) -> dict:
    """提交预测。confidence 取 argmax 概率；成功返回 201 响应体。"""
    bearish = float(probabilities["bearish"])
    neutral = float(probabilities["neutral"])
    bullish = float(probabilities["bullish"])
    probs = {"bearish": bearish, "neutral": neutral, "bullish": bullish}
    confidence = max(bearish, neutral, bullish)

    try:
        resp = await _request(
            "POST",
            f"/eval/challenges/{challenge_id}/predict",
            json_body={
                "probabilities": probs,
                "confidence": confidence,
                "summary": summary,
                "reasoning": reasoning,
            },
        )
        resp.raise_for_status()
        return resp.json() if resp.content else {}
    except httpx.HTTPError as e:
        detail = ""
        if e.response is not None:
            detail = e.response.text[:300]
        logger.warning("Headline Arena 提交预测失败 challenge=%s: %s %s", challenge_id, e, detail)
        raise RuntimeError(f"Headline Arena 提交预测失败: {e} {detail}") from e


async def get_agent_card() -> dict | None:
    """Agent 公开名片（/agent/{id}/card）：active 即返回，含名称、段位、声誉等。"""
    agent_id, _ = _credentials()
    if not agent_id:
        return None
    try:
        resp = await _get_with_retry(f"{_API}/agent/{agent_id}/card")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("Headline Arena 获取 agent card 失败: %s", e)
        return None


async def get_scorecard() -> dict | None:
    """成绩单（/eval/agents/{id}/scorecard）；首条预测结算前返回 404，归一为空态。"""
    agent_id, _ = _credentials()
    if not agent_id:
        return None
    try:
        resp = await _get_with_retry(f"{_API}/eval/agents/{agent_id}/scorecard")
        if resp.status_code == 404:
            logger.info("Headline Arena 暂无已结算成绩（%s）", agent_id)
            return None
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("Headline Arena 获取成绩单失败: %s", e)
        return None


async def get_calibration() -> dict | None:
    """校准数据（公开端点）；失败返回 None 不抛异常。"""
    agent_id, _ = _credentials()
    if not agent_id:
        return None
    try:
        resp = await _get_with_retry(f"{_API}/eval/agents/{agent_id}/calibration")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("Headline Arena 获取校准数据失败: %s", e)
        return None
