"""L5 CI 门禁：意图识别安全网评估（确定性，随 pytest 运行）

将 tests/eval_intent.py 的评估逻辑封装为 pytest 用例，
accuracy < 阈值时测试失败 → CI 失败，形成评估门禁。

确定性、无网络/API 依赖，可在 GitHub Actions 中稳定运行。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.eval_intent import GOLDEN, evaluate

GATE_THRESHOLD = 0.9


def test_intent_eval_accuracy_gate():
    result = evaluate()
    assert result["total"] == len(GOLDEN), "golden 样本数量异常"
    assert result["accuracy"] >= GATE_THRESHOLD, (
        f"意图识别安全网准确率退化 {result['accuracy']} < {GATE_THRESHOLD}，"
        f"失败样本: {result['failures']}"
    )
