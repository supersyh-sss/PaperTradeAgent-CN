"""Agent 输出质量门 — 动态路由与自愈机制

功能:
  1. check_agent_output() — 检查 Agent 输出质量，返回健康状态
  2. _route_after_quality_check() — 根据质量结果动态路由
  3. 关键 Agent 输出为空时自动标记重试
"""


from .schemas import (
    validate_agent_output,
)


class QualityStatus:
    OK = "ok"
    DEGRADED = "degraded"
    FAILED = "failed"


def check_agent_output(agent_key: str, state: dict) -> tuple[str, str, str | None]:
    """检查 Agent 输出质量

    Returns:
        (status, detail, recommended_action)
        status: "ok" | "degraded" | "failed"
        detail: 人类可读的描述
        recommended_action: "retry" | "skip" | "abort" | None
    """
    if agent_key == "chief_strategist":
        intent = state.get("intent", "")
        needed = state.get("needed_agents", [])
        if not intent:
            return QualityStatus.DEGRADED, "首席策略意图识别为空", "retry"
        if needed and intent in ("chat", "watchlist"):
            return QualityStatus.DEGRADED, "意图与 Agent 列表不匹配", "retry"
        return QualityStatus.OK, "ok", None

    if agent_key == "quant_researcher":
        output = state.get("quant_assessment")
        if output is None:
            return QualityStatus.FAILED, "量化分析输出为空", "retry"
        if output.get("parse_error"):
            return QualityStatus.FAILED, "量化分析 JSON 解析失败", "retry"
        if not output.get("trend_assessment"):
            return QualityStatus.DEGRADED, "量化分析缺少趋势评估", "retry"
        try:
            validate_agent_output("quant_researcher", output)
        except ValueError as e:
            return QualityStatus.DEGRADED, f"量化分析 schema 验证失败: {e}", "retry"
        return QualityStatus.OK, "ok", None

    if agent_key == "market_intelligence":
        output = state.get("intelligence_assessment")
        if output is None:
            return QualityStatus.FAILED, "市场情报分析输出为空", "retry"
        if output.get("parse_error"):
            return QualityStatus.FAILED, "市场情报 JSON 解析失败", "retry"
        try:
            validate_agent_output("market_intelligence", output)
        except ValueError as e:
            return QualityStatus.DEGRADED, f"市场情报 schema 验证失败: {e}", "retry"
        return QualityStatus.OK, "ok", None

    if agent_key == "trade_executor":
        output = state.get("trade_plan")
        if output is None:
            return QualityStatus.FAILED, "交易计划输出为空", "retry"
        if output.get("parse_error"):
            return QualityStatus.FAILED, "交易计划 JSON 解析失败", "retry"
        recommendation = output.get("trade_recommendation", "")
        if recommendation == "ABORT":
            return QualityStatus.DEGRADED, "交易计划建议中止", "skip"
        try:
            validate_agent_output("trade_executor", output)
        except ValueError as e:
            return QualityStatus.DEGRADED, f"交易计划 schema 验证失败: {e}", "retry"
        return QualityStatus.OK, "ok", None

    if agent_key == "portfolio_monitor":
        summary = state.get("portfolio_summary")
        if summary is None:
            return QualityStatus.FAILED, "持仓监控输出为空", "retry"

        # 空仓（positions 为空）属正常：节点不调用 LLM，也没有 portfolio_assessment
        positions = summary.get("positions") or []
        if not positions:
            return QualityStatus.OK, "ok", None

        # 有持仓时，LLM 健康度评估必须存在且 schema 合法
        assessment = state.get("portfolio_assessment")
        if not assessment:
            return QualityStatus.DEGRADED, "持仓健康度评估缺失", "retry"
        try:
            validate_agent_output("portfolio_monitor", assessment)
        except ValueError as e:
            return QualityStatus.DEGRADED, f"持仓评估 schema 验证失败: {e}", "retry"
        return QualityStatus.OK, "ok", None

    return QualityStatus.OK, "ok", None
