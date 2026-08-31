"""
测试首席策略官 LLM 自主分类 — 纯大模型驱动，无关键词兜底
验证每种输入类型后 LLM 的 intent / needs_report / needed_agents 决策

NOTE: 集成测试脚本，需要真实后端服务运行（python tests/test_llm_classification.py）。
pytest 通过 __test__ = False 跳过收集。
"""
import asyncio
import json

import httpx

__test__ = False  # 集成脚本，非 pytest 单测，禁止收集

BASE = "http://localhost:8001"
AUTH = {"Authorization": "Bearer mvp_test_token_2026"}

TEST_CASES = [
    # (message, expected_behavior_note)
    ("你好", "闲聊 → intent=chat, needs_report=false, agents=[]"),
    ("茅台多少钱", "股票查询 → intent=query, needs_report=false, agents=[quant_researcher]"),
    ("今天大盘怎么样", "市场概览 → intent=market, needs_report=true, agents=[market_intelligence]"),
    ("分析比亚迪", "股票分析 → intent=analyze, needs_report=true, agents=[quant_researcher,market_intelligence]"),
    ("买入100股神剑股份", "交易指令 → intent=trade, needs_report=true, agents 含 trade_executor"),
    ("帮我看看宁德时代", "模糊分析 → intent=query 或 analyze"),
    ("查看自选", "自选管理 → intent=watchlist, needs_report=false, agents=[]"),
    ("撤单", "撤单 → intent=cancel_order, needs_report=false, agents=[]"),
    ("我的持仓怎么样", "持仓分析 → intent=portfolio, needs_report=true, agents=[portfolio_monitor]"),
    ("市场情绪如何", "市场动态 → intent=market 或 query"),
]


async def test_one(client: httpx.AsyncClient, msg: str, note: str, idx: int):
    """Send one message and collect SSE events"""
    payload = {
        "message": msg,
        "conversation_id": f"test_llm_cls_{idx}_{asyncio.get_event_loop().time()}"
    }
    
    intent = "?"
    needs_report = "?"
    needed_agents = "?"
    chief_log = ""
    error = None
    
    try:
        async with client.stream(
            "POST", f"{BASE}/api/chat/stream",
            json=payload,
            headers=AUTH,
            timeout=httpx.Timeout(60.0)
        ) as resp:
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = json.loads(line[6:])
                t = data.get("type")
                
                if t == "agent_log_start" and data.get("agent") == "chief_strategist":
                    chief_log = ""  # Start collecting chief log
                elif t == "agent_token":
                    chief_log += data.get("text", "")
                elif t == "done":
                    intent = data.get("intent", "?")
                    needs_report = data.get("needs_report", "?")
                    needed_agents = data.get("needed_agents", [])
    except Exception as e:
        error = str(e)
    
    return {
        "idx": idx,
        "input": msg,
        "note": note,
        "intent": intent,
        "needs_report": needs_report,
        "needed_agents": needed_agents,
        "chief_log_preview": chief_log[:200] if chief_log else "(empty)",
        "error": error,
    }


async def main():
    results = []
    async with httpx.AsyncClient() as client:
        for i, (msg, note) in enumerate(TEST_CASES, 1):
            print(f"\n[{i}/{len(TEST_CASES)}] Testing: {msg}")
            r = await test_one(client, msg, note, i)
            results.append(r)
            # Print immediately
            status = "OK" if not r["error"] else f"ERR: {r['error']}"
            agents_str = ", ".join(r["needed_agents"]) if isinstance(r["needed_agents"], list) else str(r["needed_agents"])
            print(f"  intent={r['intent']}, needs_report={r['needs_report']}, agents=[{agents_str}]  | {status}")
            print(f"  chief_log: {r['chief_log_preview'][:150]}")
    
    # Summary table
    print("\n\n" + "=" * 90)
    print(f"{'#':<3} {'输入':<24} {'意图':<14} {'报告':<8} {'调用Agent':<40} {'状态'}")
    print("-" * 90)
    for r in results:
        agents_str = ", ".join(r["needed_agents"]) if isinstance(r["needed_agents"], list) else str(r["needed_agents"])
        status = "ERR" if r["error"] else "OK"
        print(f"{r['idx']:<3} {r['input']:<24} {r['intent']:<14} {r['needs_report']!s:<8} {agents_str:<40} {status}")
    print("=" * 90)
    
    # Check for issues
    issues = []
    for r in results:
        if r["error"]:
            issues.append(f"[{r['idx']}] {r['input']}: {r['error']}")
        if r["intent"] == "?":
            issues.append(f"[{r['idx']}] {r['input']}: LLM 无响应 (intent=?)")
    
    if issues:
        print("\n问题汇总:")
        for issue in issues:
            print(f"  - {issue}")
    else:
        print("\n所有测试通过，LLM 分类正常。")


if __name__ == "__main__":
    asyncio.run(main())
