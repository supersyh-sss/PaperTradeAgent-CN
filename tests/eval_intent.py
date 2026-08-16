"""L5 最小评估集：意图识别安全网准确率评测（离线、确定性、可重复）

评测对象：chief_strategist 的确定性分类组件
  - `_recover_from_chat`：LLM 误判为 chat 时的关键词安全网
  - `_INTENT_PLAN_TEMPLATE`：意图 → 任务计划模板（推导 needed_agents）

不依赖 LLM / 网络，可在任意环境重复运行，用于回归检测意图识别退化。

用法：
    uv run python tests/eval_intent.py

扩展（后续）：接入 LLM 分类结果、断言 needs_report、纳入 CI 门禁。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# golden 数据集：(输入, 期望意图, 期望 agents)
# 覆盖交易/行情/持仓/自选/闲聊五类，含易误判样本
GOLDEN = [
    # ── 交易指令 ──
    ("买入100股平安银行", "trade", ["quant_researcher", "market_intelligence", "trade_executor"]),
    ("卖出200股茅台", "trade", ["quant_researcher", "market_intelligence", "trade_executor"]),
    ("下单买入招商银行", "trade", ["quant_researcher", "market_intelligence", "trade_executor"]),
    ("挂单卖出五粮液", "trade", ["quant_researcher", "market_intelligence", "trade_executor"]),
    # ── 市场 / 行情 ──
    ("今天大盘怎么样", "market", ["market_intelligence"]),
    ("查看行情", "market", ["market_intelligence"]),
    ("市场走势如何", "market", ["market_intelligence"]),
    ("A股整体涨跌情况", "market", ["market_intelligence"]),
    ("白酒板块怎么样", "market", ["market_intelligence"]),
    ("新能源行业走势", "market", ["market_intelligence"]),
    # ── 持仓 ──
    ("查看持仓", "portfolio", ["portfolio_monitor"]),
    ("我的仓位怎么样", "portfolio", ["portfolio_monitor"]),
    ("盈亏情况如何", "portfolio", ["portfolio_monitor"]),
    ("我的账户情况", "portfolio", ["portfolio_monitor"]),
    # ── 自选管理 ──
    ("添加自选", "watchlist", []),
    ("删除自选", "watchlist", []),
    ("移除自选", "watchlist", []),
    # ── 撤单 ──
    ("帮我撤单", "cancel_order", []),
    ("取消挂单", "cancel_order", []),
    ("撤销委托订单", "cancel_order", []),
    # ── 闲聊（不应被误判为任务）──
    ("你好", "chat", []),
    ("有点累", "chat", []),
    ("谢谢", "chat", []),
    ("今天天气不错", "chat", []),
    ("你在吗", "chat", []),
]


def _expected_agents(intent: str) -> list:
    from backend.agents.chief_strategist import _INTENT_PLAN_TEMPLATE
    return [s["agent"] for s in _INTENT_PLAN_TEMPLATE.get(intent, [])]


def evaluate() -> dict:
    from backend.agents.chief_strategist import _recover_from_chat

    correct = 0
    failures = []

    for text, exp_intent, exp_agents in GOLDEN:
        got_intent = _recover_from_chat(text)
        got_agents = _expected_agents(got_intent) if got_intent not in ("chat", "watchlist", "cancel_order") else []

        intent_ok = got_intent == exp_intent
        agents_ok = got_agents == exp_agents
        if intent_ok and agents_ok:
            correct += 1
        else:
            failures.append({
                "input": text,
                "expected": (exp_intent, exp_agents),
                "got": (got_intent, got_agents),
            })

    return {
        "total": len(GOLDEN),
        "correct": correct,
        "accuracy": round(correct / len(GOLDEN), 4),
        "failures": failures,
    }


def main():
    result = evaluate()
    print("=" * 72)
    print("L5 意图识别安全网评估结果")
    print("=" * 72)
    print(f"总样本: {result['total']}  正确: {result['correct']}  准确率: {result['accuracy']*100:.1f}%")
    print("-" * 72)
    if result["failures"]:
        print("失败样本:")
        for f in result["failures"]:
            print(f"  [FAIL] {f['input']}: 期望 {f['expected']} → 实际 {f['got']}")
    else:
        print("[OK] 全部通过")
    print("=" * 72)
    return 0 if result["accuracy"] >= 0.9 else 1


if __name__ == "__main__":
    sys.exit(main())
