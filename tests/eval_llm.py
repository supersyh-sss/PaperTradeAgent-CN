"""L5 离线 LLM 评估：意图分类准确率 + LLM-as-judge 输出质量

用法（需 DEEPSEEK_API_KEY）：
    uv run python tests/eval_llm.py

不进入 CI（依赖 API、有 token 成本、结果带随机性），用于定期质量回归。
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.services.llm_judge import judge_answer_quality, judge_intent
from tests.eval_intent import GOLDEN

# 输出质量 golden 样本：(问题, 回答, 参考要点, 期望好/坏)
QUALITY_SAMPLES = [
    (
        "贵州茅台最近走势怎么样",
        "贵州茅台近5日上涨3.2%，MACD金叉，量能温和放大。",
        ["提及近期涨跌幅", "提及技术指标"],
        "good",
    ),
    (
        "贵州茅台最近走势怎么样",
        "今天天气不错，适合出门。",
        ["提及近期涨跌幅", "提及技术指标"],
        "bad",
    ),
    (
        "帮我看看平安银行",
        "平安银行当前市盈率5.8倍，市净率0.62倍，低于行业平均。",
        ["提及估值指标", "与行业对比"],
        "good",
    ),
    (
        "帮我看看平安银行",
        "平安银行股价肯定翻倍，快买！",
        ["提及估值指标", "与行业对比"],
        "bad",
    ),
    (
        "招商银行市盈率多少",
        "招商银行市盈率约5.2倍，市净率0.75倍，低于行业平均。",
        ["提及市盈率", "提及市净率"],
        "good",
    ),
    (
        "招商银行市盈率多少",
        "招商银行明天会涨停，赶紧全仓买入。",
        ["提及市盈率", "基于估值数据"],
        "bad",
    ),
    (
        "最近市场情绪怎么样",
        "今日A股三大指数涨跌互现，上涨家数与下跌家数基本持平，市场情绪偏中性。",
        ["提及指数涨跌", "提及市场情绪"],
        "good",
    ),
    (
        "最近市场情绪怎么样",
        "我不知道，你去问别人吧。",
        ["提及指数涨跌", "提及市场情绪"],
        "bad",
    ),
]


async def run_intent_eval() -> float:
    correct = 0
    fails = []
    for text, exp_intent, _ in GOLDEN:
        r = await judge_intent(text)
        got = r.get("intent")
        if got == exp_intent:
            correct += 1
        else:
            fails.append((text, exp_intent, got))
    total = len(GOLDEN)
    acc = correct / total
    print(f"[意图分类 LLM 评估] {correct}/{total} 准确率 {acc * 100:.1f}%")
    for t, e, g in fails:
        print(f"  [FAIL] {t}: 期望 {e} → 实际 {g}")
    return acc


async def run_quality_eval() -> tuple:
    good_scores, bad_scores = [], []
    for question, answer, refs, label in QUALITY_SAMPLES:
        r = await judge_answer_quality(question, answer, refs)
        overall = r.get("overall", 0)
        (good_scores if label == "good" else bad_scores).append(overall)
        print(f"  [{label}] overall={overall}  {question!r} → {r.get('reason', '')[:40]}")
    avg_good = sum(good_scores) / len(good_scores) if good_scores else 0
    avg_bad = sum(bad_scores) / len(bad_scores) if bad_scores else 0
    print(f"[输出质量 LLM-as-judge] 好样本均分 {avg_good:.1f} vs 坏样本均分 {avg_bad:.1f}")
    return avg_good, avg_bad


async def main():
    await run_intent_eval()
    print()
    await run_quality_eval()


if __name__ == "__main__":
    asyncio.run(main())
