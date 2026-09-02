"""契约注册中心 — Contract-First 原则

所有 Agent 输入/输出均需定义明确的 JSON Schema，实现：
  - 模块化：各 Agent 通过契约解耦
  - 可测试性：按契约验证输入输出
  - 可演进：契约版本化，支持平滑升级
"""

import json as json_mod
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar

logger = logging.getLogger(__name__)

# Agent Output Schemas (JSON Schema Draft-2020-12)

CHIEF_STRATEGIST_SCHEMA = {
    "type": "object",
    "required": ["intent", "needed_agents", "needs_report"],
    "properties": {
        "intent": {
            "type": "string",
            "enum": [
                "analyze",
                "trade",
                "query",
                "portfolio",
                "watchlist",
                "chat",
                "market",
                "cancel_order",
            ],
        },
        "stock_symbol": {"type": "string"},
        "stock_name": {"type": "string"},
        "needed_agents": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": [
                    "quant_researcher",
                    "market_intelligence",
                    "trade_executor",
                    "portfolio_monitor",
                ],
            },
        },
        "needs_report": {"type": "boolean"},
        "trade_side": {"type": "string", "enum": ["BUY", "SELL"]},
        "trade_quantity": {"type": "integer", "minimum": 100},
        "reasoning_chain": {"type": "string"},
        "chat_reply": {"type": "string"},
        "cancel_order_id": {"type": "string"},
        "detail_level": {"type": "string", "enum": ["detailed", "brief", "auto"]},
    },
}

QUANT_RESEARCHER_SCHEMA = {
    "type": "object",
    "required": ["summary", "trend_assessment", "strength_rating"],
    "properties": {
        "summary": {"type": "string", "maxLength": 200},
        "trend_assessment": {"type": "string"},
        "strength_rating": {"type": "number", "minimum": 0, "maximum": 10},
        "volatility_assessment": {"type": "string"},
        "key_signals": {"type": "array", "items": {"type": "string"}},
        "risk_flags": {"type": "array", "items": {"type": "string"}},
    },
}

MARKET_INTELLIGENCE_SCHEMA = {
    "type": "object",
    "required": ["sentiment_label", "sentiment_score"],
    "properties": {
        "sentiment_label": {
            "type": "string",
            "enum": ["positive", "neutral", "negative", "unknown"],
        },
        "sentiment_score": {"type": "number", "minimum": -1, "maximum": 1},
        "impact_direction": {"type": "string"},
        "impact_strength": {"type": "string", "enum": ["high", "medium", "low"]},
        "impact_summary": {"type": "string"},
        "key_factors": {"type": "array", "items": {"type": "string"}},
        "risk_alerts": {"type": "array", "items": {"type": "string"}},
    },
}

TRADE_EXECUTOR_SCHEMA = {
    "type": "object",
    "required": ["trade_recommendation", "risk_level"],
    "properties": {
        "trade_recommendation": {"type": "string"},
        "risk_level": {"type": "string", "enum": ["LOW", "NORMAL", "HIGH", "EXTREME"]},
        "risk_assessment_chinese": {"type": "string"},
        "suggested_price": {"type": "number", "minimum": 0.01},
        "suggested_quantity": {"type": "integer", "minimum": 100},
    },
}

PORTFOLIO_MONITOR_SCHEMA = {
    "type": "object",
    "required": ["portfolio_health"],
    "properties": {
        "portfolio_health": {"type": "string"},
        "concentration_risk": {"type": "string"},
        "drawdown_status": {"type": "string"},
        "recommendations_chinese": {"type": "array", "items": {"type": "string"}},
    },
}

# Contract Registry


class ContractVersion(Enum):
    V1_0 = "1.0"
    V1_1 = "1.1"


@dataclass
class AgentContract:
    """Agent 契约：定义输入/输出 Schema 及语义约束"""

    agent_name: str
    version: ContractVersion = ContractVersion.V1_0
    input_schema: dict | None = None
    output_schema: dict = field(default_factory=dict)
    description: str = ""
    max_retries: int = 3
    timeout_ms: int = 120000

    def validate_output(self, data: dict[str, Any]) -> dict[str, Any]:
        """验证 Agent 输出是否符合契约（轻量校验，非完整 JSON Schema）"""
        issues = []
        for key in self.output_schema.get("required", []):
            if key not in data:
                issues.append(f"Missing required field: {key}")

        # Type coercion for critical fields
        props = self.output_schema.get("properties", {})
        for key, prop in props.items():
            if key in data:
                expected_type = prop.get("type")
                if expected_type == "number" and not isinstance(
                    data[key], (int, float)
                ):
                    try:
                        data[key] = float(data[key])
                    except (ValueError, TypeError):
                        issues.append(
                            f"Field '{key}' should be number, got: {type(data[key]).__name__}"
                        )
                elif expected_type == "integer" and not isinstance(data[key], int):
                    try:
                        data[key] = int(data[key])
                    except (ValueError, TypeError):
                        issues.append(
                            f"Field '{key}' should be integer, got: {type(data[key]).__name__}"
                        )
                elif expected_type == "array" and not isinstance(data[key], list):
                    try:
                        if isinstance(data[key], str):
                            data[key] = json_mod.loads(data[key])
                    except json_mod.JSONDecodeError:
                        issues.append(f"Field '{key}' should be array")
        return data, issues


class ContractRegistry:
    """全局契约注册表 — 单例模式"""

    _instance = None
    _contracts: ClassVar[dict[str, AgentContract]] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_defaults()
        return cls._instance

    def _init_defaults(self):
        """注册所有 Agent 的默认契约"""
        self._contracts["chief_strategist"] = AgentContract(
            agent_name="chief_strategist",
            output_schema=CHIEF_STRATEGIST_SCHEMA,
            description="首席策略官：意图识别与任务拆解",
            max_retries=2,
            timeout_ms=60000,
        )
        self._contracts["quant_researcher"] = AgentContract(
            agent_name="quant_researcher",
            output_schema=QUANT_RESEARCHER_SCHEMA,
            description="量化研究员：技术分析与指标解读",
            max_retries=3,
            timeout_ms=120000,
        )
        self._contracts["market_intelligence"] = AgentContract(
            agent_name="market_intelligence",
            output_schema=MARKET_INTELLIGENCE_SCHEMA,
            description="市场动态感知官：舆情分析与情绪评估",
            max_retries=2,
            timeout_ms=60000,
        )
        self._contracts["trade_executor"] = AgentContract(
            agent_name="trade_executor",
            output_schema=TRADE_EXECUTOR_SCHEMA,
            description="交易执行员：定价与风险评估",
            max_retries=3,
            timeout_ms=120000,
        )
        self._contracts["portfolio_monitor"] = AgentContract(
            agent_name="portfolio_monitor",
            output_schema=PORTFOLIO_MONITOR_SCHEMA,
            description="风控持仓监控官：持仓健康度评估",
            max_retries=2,
            timeout_ms=120000,
        )

    def get(self, agent_name: str) -> AgentContract | None:
        """获取 Agent 契约"""
        return self._contracts.get(agent_name)

    def register(self, agent_name: str, contract: AgentContract):
        """注册/更新 Agent 契约"""
        self._contracts[agent_name] = contract
        logger.info(f"Contract registered: {agent_name} v{contract.version.value}")

    def validate(
        self, agent_name: str, output: dict[str, Any]
    ) -> tuple[dict[str, Any], list]:
        """验证 Agent 输出是否符合契约"""
        contract = self.get(agent_name)
        if not contract:
            return output, [f"No contract defined for: {agent_name}"]
        return contract.validate_output(output)
