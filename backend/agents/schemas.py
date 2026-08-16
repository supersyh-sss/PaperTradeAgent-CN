"""Agent 输出 Pydantic Schema — 结构化验证每个 Agent 的 LLM 输出

与 prompts.py 中的 JSON 输出格式严格对应。
每个 Agent 节点调用 LLM 后，用对应 Schema 验证输出结构。
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional
from datetime import datetime


class ChiefOutput(BaseModel):
    intent: str = Field(
        ...,
        pattern=r"^(chat|query|market|analyze|trade|portfolio|watchlist|cancel_order|direct_agent)$"
    )
    stock_symbol: Optional[str] = None
    stock_name: Optional[str] = None
    trade_side: Optional[str] = Field(None, pattern=r"^(BUY|SELL)$")
    trade_quantity: Optional[int] = Field(None, ge=100)
    confidence: float = Field(ge=0.0, le=1.0)
    needs_report: bool
    needed_agents: list[str] = Field(default_factory=list)
    reasoning_chain: str = ""
    chat_reply: str = ""
    detail_level: str = Field("auto", pattern=r"^(detailed|brief|auto)$")
    cancel_order_id: str = ""
    direct_agent: str = ""

    @property
    def has_stock(self) -> bool:
        return bool(self.stock_symbol and self.stock_name)

    @property
    def dispatch_agents(self) -> list[str]:
        return self.needed_agents if self.direct_agent not in self.needed_agents else [self.direct_agent]


class QuantAssessment(BaseModel):
    summary: str = ""
    trend_assessment: str = Field(pattern=r"^(bullish|bearish|neutral)$")
    key_signals: list[str] = Field(default_factory=list)
    strength_rating: int = Field(ge=1, le=10)
    volatility_assessment: str = Field(pattern=r"^(low|moderate|high|extreme)$")
    support_resistance_note: str = ""
    risk_flags: list[str] = Field(default_factory=list)


class IntelligenceAssessment(BaseModel):
    sentiment_score: float = Field(ge=-1.0, le=1.0)
    sentiment_label: str = ""
    impact_direction: str = Field(pattern=r"^(positive|neutral|negative)$")
    impact_strength: str = Field(pattern=r"^(strong|moderate|weak)$")
    impact_summary: str = ""
    key_factors: list[str] = Field(default_factory=list)
    risk_alerts: list[str] = Field(default_factory=list)
    opportunity_signals: list[str] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════
# 交易执行输出
# ═══════════════════════════════════════════════════════════
class TradePlan(BaseModel):
    trade_recommendation: str = Field(pattern=r"^(PROCEED|CAUTION|ABORT)$")
    risk_level: str = Field(pattern=r"^(LOW|MEDIUM|HIGH|CRITICAL)$")
    prepared_quantity: int = Field(ge=100)
    prepared_price: float = Field(ge=0.01)
    is_within_trading_hours: bool = False
    price_adjustment_note: str = ""
    price_reasonableness_check: str = Field(pattern=r"^(VERIFIED|WARNING|REJECT)$")
    risk_assessment_chinese: str = ""
    warnings_chinese: list[str] = Field(default_factory=list)
    recommendations_chinese: list[str] = Field(default_factory=list)
    t1_restriction_note: str = ""


class PortfolioAssessment(BaseModel):
    portfolio_health: str = Field(pattern=r"^(HEALTHY|CAUTION|UNHEALTHY|CRITICAL)$")
    total_assets_chinese: str = ""
    pnl_summary_chinese: str = ""
    diversification_score: float = Field(ge=0.0, le=1.0)
    max_single_position_pct: float = Field(ge=0.0, le=100.0)
    concentration_risk: str = Field(pattern=r"^(LOW|MEDIUM|HIGH)$")
    drawdown_status: str = Field(pattern=r"^(normal|warning|danger)$")
    recommendations_chinese: list[str] = Field(default_factory=list)
    risk_flags_chinese: list[str] = Field(default_factory=list)


class AgentLog(BaseModel):
    agent: str
    name_cn: str = ""
    color: str = "#888"
    content: str = ""
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


_AGENT_VALIDATORS = {
    "chief_strategist": ChiefOutput,
    "quant_researcher": QuantAssessment,
    "market_intelligence": IntelligenceAssessment,
    "trade_executor": TradePlan,
    "portfolio_monitor": PortfolioAssessment,
}


def validate_agent_output(agent_key: str, data: dict) -> dict:
    """验证 Agent 输出并返回规范化后的 dict

    成功: 返回 Pydantic model.dict()
    失败: 抛出 ValueError 并附带验证错误详情
    """
    model_cls = _AGENT_VALIDATORS.get(agent_key)
    if model_cls is None:
        return data  # 未注册的 agent 不做验证

    validated = model_cls(**data)
    return validated.model_dump()
