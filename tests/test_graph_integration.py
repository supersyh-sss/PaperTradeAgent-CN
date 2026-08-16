"""图结构集成测试（确定性，不依赖 LLM / 网络）"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_graph_compiles():
    """LangGraph 图可编译，且包含核心节点。"""
    from backend.agents.graph import trading_graph
    assert trading_graph.nodes, "图未包含任何节点"


def test_direct_agent_detection():
    """直接点名 Agent 的确定性检测（与 test_direct_agent.py 互补的核心样本）。"""
    from backend.agents.chief_strategist import _detect_direct_agent
    cases = [
        ("量化研究员在哪", "quant_researcher"),
        ("首席策略官说个话", "chief_strategist"),
        ("分析茅台", ""),
        ("风控，看看仓位", "portfolio_monitor"),
        ("quant说句话", "quant_researcher"),
    ]
    for text, expected in cases:
        primary, _ = _detect_direct_agent(text)
        assert primary == expected, f"{text!r}: expected {expected!r}, got {primary!r}"
