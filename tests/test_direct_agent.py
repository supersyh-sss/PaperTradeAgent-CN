"""直接点名 Agent 检测测试（确定性，无 LLM / 网络依赖）

测试对象：`backend.agents.chief_strategist._detect_direct_agent`，
覆盖 `@agent`、中文别名、身份疑问句（"你是XX吗"/"你真不是XX吧"）等场景。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.agents.chief_strategist import _detect_direct_agent

CASES = [
    ("量化研究员在哪", "quant_researcher"),
    ("量化研究员，说个话", "quant_researcher"),
    ("首席策略官说个话", "chief_strategist"),
    ("市场动态感知官在哪", "market_intelligence"),
    ("你不是首席策略官吧", "chief_strategist"),
    ("你是量化研究员吗", "quant_researcher"),
    ("你不是交易员吧", "trade_executor"),
    ("风控，看看仓位", "portfolio_monitor"),
    ("@quant 来分析一下", "quant_researcher"),
    ("hello世界", ""),
    ("分析茅台", ""),
    ("quant说句话", "quant_researcher"),
    ("问交易员一个问题", "trade_executor"),
    ("portfolio 我的持仓", "portfolio_monitor"),
    ("intel 最近有什么消息", "market_intelligence"),
    ("助理在吗", ""),
    ("你真的是首席策略官吗", "chief_strategist"),
    ("你真不是风控吧", "portfolio_monitor"),
]


@pytest.mark.parametrize("text,expected", CASES)
def test_direct_agent_detection(text, expected):
    primary, _ = _detect_direct_agent(text)
    assert primary == expected
