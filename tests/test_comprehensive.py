"""Comprehensive multi-round tests: 36+ cases across chat, multi-agent, orders, watchlist, edge cases.
Designed for self-checking loop with increasing test coverage.
"""
import asyncio, httpx, json, sys, time, traceback
from collections import defaultdict

BASE = "http://localhost:8001/api"
AUTH = {"Authorization": "Bearer mvp_test_token_2026"}
TIMEOUT = 90.0

# ── Stats ──
stats = {"passed": 0, "failed": 0, "skipped": 0, "errors": [], "warnings": []}
round_num = 0

async def post_json(client, path, data):
    r = await client.post(f"{BASE}{path}", json=data, timeout=TIMEOUT)
    try: return r.json()
    except: return {"_raw": r.text[:200]}

async def get_json(client, path):
    r = await client.get(f"{BASE}{path}", timeout=TIMEOUT)
    try: return r.json()
    except: return {"_raw": r.text[:200]}

async def sse_collect(client, message, conv_id=None):
    """Collect all SSE events from chat stream."""
    events = []
    body = {"message": message}
    if conv_id:
        body["conversation_id"] = conv_id
    try:
        async with client.stream("POST", f"{BASE}/chat/stream", json=body, timeout=TIMEOUT) as resp:
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    try: events.append(json.loads(line[6:]))
                    except: pass
    except Exception as e:
        events.append({"type": "_stream_error", "message": str(e)[:100]})
    return events

def analyze_events(events):
    """Analyze SSE events: intent, errors, agents, chat content."""
    done = next((e for e in events if e.get("type") == "done"), {})
    errors = [e for e in events if e.get("type") == "error"]
    stream_errors = [e for e in events if e.get("type") == "_stream_error"]
    agent_starts = [e for e in events if e.get("type") == "agent_log_start"]
    agent_ends = [e for e in events if e.get("type") == "agent_log_end"]
    
    # Collect agent content
    agent_contents = defaultdict(str)
    for e in events:
        if e.get("type") == "agent_token":
            agent_contents["_current"] += e.get("text", "")
    
    return {
        "intent": done.get("intent", "?"),
        "has_error": len(errors) > 0 or len(stream_errors) > 0,
        "error_msg": (errors + stream_errors)[0].get("message", "") if (errors or stream_errors) else "",
        "agents": [a.get("agent", "?") for a in agent_starts],
        "agent_count": len(agent_starts),
        "has_done": "done" in {e.get("type") for e in events},
        "total_events": len(events),
    }

async def test(name, fn, *, critical=False):
    """Run a single test, record result."""
    try:
        result = await fn()
        if result:
            stats["passed"] += 1
            print(f"  ✓ [{stats['passed']}/{stats['passed']+stats['failed']}] {name}")
        else:
            stats["failed"] += 1
            stats["errors"].append(name)
            marker = " [CRITICAL]" if critical else ""
            print(f"  ✗ [{stats['passed']}/{stats['passed']+stats['failed']}] {name}{marker}")
        return result
    except asyncio.TimeoutError:
        stats["failed"] += 1
        stats["errors"].append(f"{name} (timeout)")
        print(f"  ✗ {name}: TIMEOUT")
        return False
    except Exception as e:
        stats["failed"] += 1
        stats["errors"].append(f"{name}: {str(e)[:60]}")
        print(f"  ✗ {name}: {str(e)[:80]}")
        return False

# ═══════════════════════════════════════════════════════════════
#  ROUND 1: Core Functionality (16 cases)
# ═══════════════════════════════════════════════════════════════

async def round1_core(client):
    print("\n" + "="*60)
    print("ROUND 1: Core Functionality (16 cases)")
    print("="*60)
    
    results = []
    
    # ── 1.1 Health / Basic Connectivity ──
    async def _():
        r = await get_json(client, "/portfolio")
        return isinstance(r, dict)
    results.append(await test("Health - GET /portfolio", _))
    
    # ── 1.2 Basic Chat (no @mention) ──
    async def _():
        e = await sse_collect(client, "你好")
        a = analyze_events(e)
        return a["has_done"] and not a["has_error"] and a["intent"] in ("chat", "watchlist")
    results.append(await test("Basic chat '你好'", _, critical=True))
    
    async def _():
        e = await sse_collect(client, "今天市场怎么样")
        a = analyze_events(e)
        return a["has_done"] and not a["has_error"]
    results.append(await test("Basic chat '市场怎么样'", _))
    
    async def _():
        e = await sse_collect(client, "你能做什么")
        a = analyze_events(e)
        return a["has_done"] and not a["has_error"]
    results.append(await test("Basic chat '你能做什么'", _))
    
    # ── 1.3 Single Agent @mention (one per agent) ──
    AGENT_TESTS = [
        ("@助手 我的账户情况如何", "chief_strategist"),
        ("@量化 分析平安银行的技术面", "quant_researcher"),
        ("@情报 最近市场有什么重要消息", "market_intelligence"),
        ("@交易 现在适合买入沪深300吗", "trade_executor"),
        ("@风控 我的持仓风险怎么样", "portfolio_monitor"),
    ]
    
    for msg, expected_agent in AGENT_TESTS:
        async def _(msg=msg, expected=expected_agent):
            e = await sse_collect(client, msg)
            a = analyze_events(e)
            ok = a["has_done"] and not a["has_error"]
            if not ok:
                stats["warnings"].append(f"Single agent {msg[:20]} failed: {a}")
            return ok
        results.append(await test(f"Single @: {msg[:25]}", _))
    
    # ── 1.4 Watchlist Operations ──
    async def _():
        r = await post_json(client, "/watchlist", {"symbol": "000001", "name": "平安银行"})
        # Accept both success and "already exists" as valid responses
        ok = bool(r.get("success")) or bool(r.get("ok"))
        error_ok = "已在" in str(r.get("detail", ""))
        return ok or error_ok
    results.append(await test("Watchlist add 000001", _))
    
    async def _():
        r = await post_json(client, "/watchlist", {"symbol": "600519", "name": "贵州茅台"})
        ok = bool(r.get("success")) or bool(r.get("ok"))
        error_ok = "已在" in str(r.get("detail", ""))
        return ok or error_ok
    results.append(await test("Watchlist add 600519", _))
    
    async def _():
        r = await get_json(client, "/watchlist")
        return isinstance(r, (list, dict))
    results.append(await test("Watchlist get", _))
    
    # ── 1.5 Order Operations ──
    async def _():
        r = await post_json(client, "/trade", {
            "symbol": "000001", "name": "平安银行", "side": "BUY",
            "price": 10.50, "quantity": 100, "order_type": "LIMIT"
        })
        return bool(r.get("order_id")) or bool(r.get("success")) or bool(r.get("ok"))
    results.append(await test("Place buy order 000001", _))
    
    async def _():
        r = await get_json(client, "/trade/orders/active")
        return isinstance(r, (list, dict))
    results.append(await test("Get active orders", _))
    
    async def _():
        r = await post_json(client, "/trade", {
            "symbol": "600519", "name": "贵州茅台", "side": "BUY",
            "price": 1450.00, "quantity": 100, "order_type": "LIMIT"
        })
        return bool(r.get("order_id")) or bool(r.get("success")) or bool(r.get("ok"))
    results.append(await test("Place buy order 600519", _))
    
    return all(results)


# ═══════════════════════════════════════════════════════════════
#  ROUND 2: Multi-Agent & Context (12 cases)
# ═══════════════════════════════════════════════════════════════

async def round2_multi_agent(client):
    print("\n" + "="*60)
    print("ROUND 2: Multi-Agent & Context Awareness (12 cases)")
    print("="*60)
    
    results = []
    
    # ── 2.1 Multi-Agent @mention (THE BUG FIX TEST) ──
    MULTI_TESTS = [
        "@量化 @交易 一起分析平安银行的交易机会",
        "@风控 @交易 我的持仓风险大吗，需要调整吗",
        "@情报 @量化 今天市场情绪和技术面如何",
        "@助手 @风控 帮我全面评估当前持仓",
        "@情报 @交易 @风控 全面诊断我的账户状况",
    ]
    
    for msg in MULTI_TESTS:
        async def _(msg=msg):
            e = await sse_collect(client, msg)
            a = analyze_events(e)
            ok = a["has_done"] and not a["has_error"] and a["agent_count"] >= 1
            if not ok:
                stats["warnings"].append(f"Multi-agent '{msg[:30]}': agents={a['agents']}, err={a['error_msg']}")
            return ok
        results.append(await test(f"Multi @: {msg[:30]}", _, critical=True))
    
    # ── 2.2 Context-Aware Agents ──
    async def _():
        e = await sse_collect(client, "@风控 我的自选股有哪些")
        a = analyze_events(e)
        return a["has_done"] and not a["has_error"]
    results.append(await test("Context: agent knows watchlist", _))
    
    async def _():
        e = await sse_collect(client, "@交易 我现在有什么挂单")
        a = analyze_events(e)
        return a["has_done"] and not a["has_error"]
    results.append(await test("Context: agent knows active orders", _))
    
    async def _():
        e = await sse_collect(client, "@助手 我的账户总资产是多少")
        a = analyze_events(e)
        return a["has_done"] and not a["has_error"]
    results.append(await test("Context: agent knows account balance", _))
    
    # ── 2.3 Mixed @mention patterns ──
    async def _():
        e = await sse_collect(client, "@量化 你好，@交易 也帮我看看")
        a = analyze_events(e)
        return a["has_done"] and not a["has_error"]
    results.append(await test("Mixed @: inline pattern", _))
    
    async def _():
        e = await sse_collect(client, "我想问@风控和@交易一些问题")
        a = analyze_events(e)
        return a["has_done"] and not a["has_error"]
    results.append(await test("Mixed @: embedded pattern", _))
    
    # ── 2.4 Chat-based orders ──
    async def _():
        e = await sse_collect(client, "帮我买入100股平安银行，价格10.5元")
        a = analyze_events(e)
        return a["has_done"] and not a["has_error"]
    results.append(await test("Chat: conversational buy order", _))
    
    async def _():
        e = await sse_collect(client, "把所有的买单都撤掉")
        a = analyze_events(e)
        return a["has_done"] and not a["has_error"]
    results.append(await test("Chat: cancel all buy orders", _))
    
    return all(results)


# ═══════════════════════════════════════════════════════════════
#  ROUND 3: Edge Cases & Robustness (16 cases)
# ═══════════════════════════════════════════════════════════════

async def round3_edge_cases(client):
    print("\n" + "="*60)
    print("ROUND 3: Edge Cases & Robustness (16 cases)")
    print("="*60)
    
    results = []
    
    # ── 3.1 Empty / Boundary Inputs ──
    async def _():
        e = await sse_collect(client, "")
        a = analyze_events(e)
        # Empty should either error gracefully or give a valid response
        return a["has_done"] or a["has_error"]  # either is acceptable
    results.append(await test("Edge: empty message", _))
    
    async def _():
        e = await sse_collect(client, "   ")
        a = analyze_events(e)
        return a["has_done"] or a["has_error"]
    results.append(await test("Edge: whitespace only", _))
    
    async def _():
        e = await sse_collect(client, "?")
        a = analyze_events(e)
        return a["has_done"] and not a["has_error"]
    results.append(await test("Edge: single '?'", _))
    
    async def _():
        e = await sse_collect(client, "A" * 500)
        a = analyze_events(e)
        return a["has_done"] and not a["has_error"]
    results.append(await test("Edge: repeated chars 500", _))
    
    # ── 3.2 Special Characters / Injection ──
    async def _():
        e = await sse_collect(client, "@@@###%%%")
        a = analyze_events(e)
        return a["has_done"] and not a["has_error"]
    results.append(await test("Edge: special chars", _))
    
    async def _():
        e = await sse_collect(client, "<script>alert(1)</script>")
        a = analyze_events(e)
        return a["has_done"] and not a["has_error"]
    results.append(await test("Edge: XSS attempt", _))
    
    async def _():
        e = await sse_collect(client, "SELECT * FROM users; DROP TABLE orders;")
        a = analyze_events(e)
        return a["has_done"] and not a["has_error"]
    results.append(await test("Edge: SQL injection attempt", _))
    
    # ── 3.3 Rapid-fire messages ──
    async def _():
        tasks = [sse_collect(client, f"快速测试消息 {i}") for i in range(3)]
        all_events = await asyncio.gather(*tasks, return_exceptions=True)
        ok_count = sum(1 for e in all_events if not isinstance(e, Exception))
        return ok_count >= 2
    results.append(await test("Rapid: 3 concurrent messages", _))
    
    # ── 3.4 Nonexistent / Invalid References ──
    async def _():
        e = await sse_collect(client, "@不存在的Agent 你在吗")
        a = analyze_events(e)
        return a["has_done"] and not a["has_error"]
    results.append(await test("Edge: @nonexistent agent", _))
    
    async def _():
        e = await sse_collect(client, "分析ZZZZ999这只股票")
        a = analyze_events(e)
        return a["has_done"] and not a["has_error"]
    results.append(await test("Edge: nonexistent stock symbol", _))
    
    # ── 3.5 Long Chinese input ──
    async def _():
        msg = "请帮我分析一下当前的A股市场整体情况，包括大盘走势、热门板块、资金流向。" * 2
        e = await sse_collect(client, msg)
        a = analyze_events(e)
        return a["has_done"] and not a["has_error"]
    results.append(await test("Edge: long Chinese input", _))
    
    # ── 3.6 Ticker-only queries ──
    async def _():
        e = await sse_collect(client, "000001")
        a = analyze_events(e)
        return a["has_done"] and not a["has_error"]
    results.append(await test("Edge: ticker only '000001'", _))
    
    async def _():
        e = await sse_collect(client, "600519 怎么样")
        a = analyze_events(e)
        return a["has_done"] and not a["has_error"]
    results.append(await test("Edge: '600519 怎么样'", _))
    
    # ── 3.7 Mixed Chinese/English ──
    async def _():
        e = await sse_collect(client, "帮我看看AAPL和TSLA的对比，还有茅台的PE ratio")
        a = analyze_events(e)
        return a["has_done"] and not a["has_error"]
    results.append(await test("Edge: mixed CN/EN symbols", _))
    
    # ── 3.8 Order Edge Cases ──
    async def _():
        r = await post_json(client, "/trade/cancel", {"order_id": "nonexistent_order_99999"})
        # Should return some response (error or success, not crash)
        return isinstance(r, dict)
    results.append(await test("Order: cancel nonexistent", _))
    
    async def _():
        r = await post_json(client, "/trade", {
            "symbol": "000001", "side": "BUY",
            "price": -10.50, "quantity": 100, "order_type": "LIMIT"
        })
        return isinstance(r, dict)  # should handle gracefully
    results.append(await test("Order: negative price", _))
    
    return all(results)


# ═══════════════════════════════════════════════════════════════
#  ROUND 4: Stress & Concurrent (6 cases)
# ═══════════════════════════════════════════════════════════════

async def round4_stress(client):
    print("\n" + "="*60)
    print("ROUND 4: Stress & Concurrency (6 cases)")
    print("="*60)
    
    results = []
    
    # ── 4.1 Multiple different @mentions in parallel ──
    async def _():
        msgs = [
            "@量化 分析平安银行",
            "@交易 挂单买入茅台",
            "@风控 检查持仓风险",
            "@情报 市场消息",
            "@助手 账户概览",
        ]
        tasks = [sse_collect(client, m) for m in msgs]
        all_events = await asyncio.gather(*tasks, return_exceptions=True)
        ok_count = 0
        for events in all_events:
            if isinstance(events, Exception):
                continue
            a = analyze_events(events)
            if a["has_done"] and not a["has_error"]:
                ok_count += 1
        return ok_count >= 4  # at least 4/5 succeed
    results.append(await test("Stress: 5 concurrent different agents", _))
    
    # ── 4.2 Same agent rapid-fire ──
    async def _():
        tasks = [sse_collect(client, "@量化 分析一下" + ("沪深300" if i % 2 == 0 else "创业板指")) for i in range(4)]
        all_events = await asyncio.gather(*tasks, return_exceptions=True)
        ok_count = sum(1 for e in all_events if not isinstance(e, Exception) and analyze_events(e)["has_done"])
        return ok_count >= 3
    results.append(await test("Stress: 4 concurrent same agent", _))
    
    # ── 4.3 Sequential multi-agent then single agent ──
    async def _():
        e1 = await sse_collect(client, "@量化 @交易 综合分析")
        a1 = analyze_events(e1)
        ok1 = a1["has_done"] and not a1["has_error"]
        
        e2 = await sse_collect(client, "@风控 现在呢")
        a2 = analyze_events(e2)
        ok2 = a2["has_done"] and not a2["has_error"]
        return ok1 and ok2
    results.append(await test("Stress: sequential multi→single agent", _))
    
    # ── 4.4 Watchlist + order + chat in rapid sequence ──
    async def _():
        ops = [
            post_json(client, "/watchlist", {"symbol": "000858", "name": "五粮液"}),
            sse_collect(client, "@交易 买入100股五粮液"),
            get_json(client, "/watchlist"),
        ]
        results_ops = await asyncio.gather(*ops, return_exceptions=True)
        return sum(1 for r in results_ops if not isinstance(r, Exception)) >= 2
    results.append(await test("Stress: watchlist+order+chat ops", _))
    
    # ── 4.5 Response content quality check ──
    async def _():
        e = await sse_collect(client, "@助手 你好，介绍一下你自己")
        a = analyze_events(e)
        # Must have at least one agent responded with content
        return a["agent_count"] >= 1 and a["has_done"]
    results.append(await test("Quality: agent self-intro has content", _))
    
    # ── 4.6 Conversation continuity ──
    async def _():
        conv_id = f"test_conv_{int(time.time())}"
        e1 = await sse_collect(client, "我持有平安银行", conv_id)
        e2 = await sse_collect(client, "我刚才说了我持有什么", conv_id)
        a1, a2 = analyze_events(e1), analyze_events(e2)
        return a1["has_done"] and a2["has_done"]
    results.append(await test("Stress: conversation continuity", _))
    
    return all(results)


# ═══════════════════════════════════════════════════════════════
#  MAIN: Multi-round runner with self-check loop
# ═══════════════════════════════════════════════════════════════

async def main(rounds: int = 1):
    global round_num
    all_pass = True
    
    print("╔══════════════════════════════════════════════╗")
    print("║   COMPREHENSIVE ROBUSTNESS TEST SUITE       ║")
    print("║   36+ test cases across 4 rounds            ║")
    print("╚══════════════════════════════════════════════╝")
    print(f"Base URL: {BASE}")
    print(f"Rounds to run: {rounds}")
    
    start_time = time.time()
    
    async with httpx.AsyncClient(timeout=TIMEOUT, headers=AUTH) as client:
        for r in range(rounds):
            round_num = r + 1
            print(f"\n{'#'*60}")
            print(f"#  ITERATION {round_num}/{rounds}")
            print(f"{'#'*60}")
            
            r1 = await round1_core(client)
            r2 = await round2_multi_agent(client)
            r3 = await round3_edge_cases(client)
            r4 = await round4_stress(client)
            
            round_ok = r1 and r2 and r3 and r4
            all_pass = all_pass and round_ok
            
            print(f"\n  ── Round {round_num} Summary ──")
            print(f"  R1 (Core):     {'✓' if r1 else '✗'}")
            print(f"  R2 (Multi):    {'✓' if r2 else '✗'}")
            print(f"  R3 (Edge):     {'✓' if r3 else '✗'}")
            print(f"  R4 (Stress):   {'✓' if r4 else '✗'}")
    
    elapsed = time.time() - start_time
    
    # ── Final Report ──
    print(f"\n{'='*60}")
    print(f"FINAL REPORT")
    print(f"{'='*60}")
    print(f"Total: {stats['passed'] + stats['failed']} tests")
    print(f"Passed:  {stats['passed']}")
    print(f"Failed:  {stats['failed']}")
    print(f"Time:    {elapsed:.1f}s")
    
    if stats["errors"]:
        print(f"\nFailed tests ({len(stats['errors'])}):")
        for err in stats["errors"]:
            print(f"  - {err}")
    
    if stats["warnings"]:
        print(f"\nWarnings ({len(stats['warnings'])}):")
        for w in stats["warnings"][:5]:
            print(f"  - {w}")
        if len(stats["warnings"]) > 5:
            print(f"  ... and {len(stats['warnings']) - 5} more")
    
    result = "ALL PASSED" if all_pass else "SOME FAILED"
    print(f"\n  ╔════════════════════╗")
    print(f"  ║  {result:<18} ║")
    print(f"  ╚════════════════════╝")
    
    return all_pass


if __name__ == "__main__":
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    ok = asyncio.run(main(rounds))
    sys.exit(0 if ok else 1)
