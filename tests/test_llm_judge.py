"""L5 LLM-as-judge 确定性单元测试（不调 API，仅验证 prompt 构造与容错路径）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.services.llm_judge import build_intent_messages, build_quality_messages


def test_build_intent_messages_contains_enum_and_json_schema():
    msgs = build_intent_messages("买入100股茅台")
    assert msgs[0]["role"] == "system"
    assert "intent" in msgs[0]["content"]
    assert "confidence" in msgs[0]["content"]
    assert msgs[1]["role"] == "user"
    assert msgs[1]["content"] == "买入100股茅台"


def test_build_quality_messages_contains_dimensions_and_refs():
    msgs = build_quality_messages("贵州茅台怎么样", "近期上涨3%", ["提及涨跌幅", "提及技术指标"])
    sys_content = msgs[0]["content"]
    for dim in ("relevance", "completeness", "factuality", "format_ok", "overall", "issues"):
        assert dim in sys_content
    user_content = msgs[1]["content"]
    assert "贵州茅台怎么样" in user_content
    assert "提及涨跌幅" in user_content
    assert "提及技术指标" in user_content
    assert "近期上涨3%" in user_content
