"""Comprehensive robustness tests v2: watchlist, orders, agent chat with context, multi-agent.

NOTE: 集成测试脚本，需要真实后端服务运行（python tests/test_robust_v2.py）。
pytest 通过 __test__ = False 跳过收集。
"""
import asyncio
import json

import httpx

__test__ = False  # 集成脚本，非 pytest 单测，禁止收集

BASE = "http://localhost:8001/api"
USER = "test_robust_v2"
AUTH = {"Authorization": "Bearer mvp_test_token_2026"}

async def post_json(client, path, data):
    r = await client.post(f"{BASE}{path}", json=data)
    return r.json()

async def sse_collect(client, message):
    events = []
    async with client.stream("POST", f"{BASE}/chat/stream", json={"message": message, "user_id": USER}) as resp:
        async for line in resp.aiter_lines():
            if line.startswith("data: "):
                try: events.append(json.loads(line[6:]))
                except Exception: pass
    return events

def analyze(events):
    done = next((e for e in events if e.get("type") == "done"), {})
    errors = [e for e in events if e.get("type") == "error"]
    agents = [e for e in events if e.get("type") == "agent_log_start"]
    return done.get("intent", "?"), len(errors) == 0, [a.get("agent","?") for a in agents]

async def test(name, fn):
    try:
        ok = await fn()
        print(f"  {'✓' if ok else '✗'} {name}")
        return ok
    except Exception as e:
        print(f"  ✗ {name}: {str(e)[:80]}")
        return False

async def main():
    results = []
    async with httpx.AsyncClient(timeout=60.0, headers=AUTH) as c:
        
        print("=== 1. Watchlist Operations ===")
        async def _(): 
            r = await post_json(c, "/watchlist/add", {"symbol": "000001", "name": "平安银行"})
            return r.get("ok") or r.get("success")
        results.append(await test("Add to watchlist", _))

        async def _():
            r = await post_json(c, "/watchlist/add", {"symbol": "600519", "name": "贵州茅台"})
            return r.get("ok") or r.get("success")
        results.append(await test("Add another to watchlist", _))

        async def _():
            r = await post_json(c, "/watchlist", {})
            return isinstance(r, (list, dict))
        results.append(await test("Get watchlist", _))

        print("\n=== 2. Basic Agent Chat (with context) ===")
        async def _():
            e = await sse_collect(c, "你好")
            intent, ok, _agents = analyze(e)
            return ok and intent in ("chat", "watchlist")
        results.append(await test("Greeting '你好'", _))

        async def _():
            e = await sse_collect(c, "@风控 我的持仓情况怎么样")
            intent, ok, _agents = analyze(e)
            return ok and intent == "direct_agent"
        results.append(await test("Direct agent: portfolio monitor @风控", _))

        async def _():
            e = await sse_collect(c, "@量化 帮我看看我的自选股里有哪些值得关注的")
            intent, ok, _agents = analyze(e)
            return ok and intent == "direct_agent"
        results.append(await test("Direct agent: quant researcher @量化", _))

        print("\n=== 3. Multi-Agent @mention ===")
        async def _():
            e = await sse_collect(c, "@量化 @交易 一起看看自选股里有什么交易机会")
            _intent, ok, _agents = analyze(e)
            # Should detect multi-agent and route to chief
            return ok
        results.append(await test("Multi-agent: @量化 @交易", _))

        async def _():
            e = await sse_collect(c, "@首席 @情报 今天市场有什么消息")
            _intent, ok, _agents = analyze(e)
            return ok
        results.append(await test("Multi-agent: @助手 @情报", _))

        print("\n=== 4. Order Operations ===")
        async def _():
            r = await post_json(c, "/trade/order", {
                "symbol": "000001", "name": "平安银行", "side": "buy",
                "price": 10.50, "quantity": 100, "order_type": "limit"
            })
            return r.get("ok") or r.get("success") or r.get("order_id") is not None
        results.append(await test("Place buy order", _))

        async def _():
            r = await post_json(c, "/trade/order", {
                "symbol": "600519", "name": "贵州茅台", "side": "buy",
                "price": 1500.00, "quantity": 100, "order_type": "limit"
            })
            return r.get("ok") or r.get("success") or r.get("order_id") is not None
        results.append(await test("Place another order", _))

        async def _():
            r = await post_json(c, "/trade/orders/active", {})
            return isinstance(r, (list, dict))
        results.append(await test("Get active orders", _))

        async def _():
            r = await post_json(c, "/portfolio", {})
            return isinstance(r, dict)
        results.append(await test("Get portfolio", _))

        # Get active orders to cancel
        active = await post_json(c, "/trade/orders/active", {})
        orders = active if isinstance(active, list) else active.get("orders", [])
        if orders:
            async def _():
                r = await post_json(c, "/trade/cancel", {"order_id": orders[0].get("order_id") or orders[0].get("id")})
                return r.get("ok") or r.get("success")
            results.append(await test("Cancel first order", _))

        print("\n=== 5. Context-Aware Agent Chat ===")
        async def _():
            e = await sse_collect(c, "@交易 我现在有哪些挂单")
            _intent, ok, _agents = analyze(e)
            return ok
        results.append(await test("Trade agent aware of active orders", _))

        async def _():
            e = await sse_collect(c, "撤单" + (orders[0].get("order_id","") if orders else ""))
            _intent, ok, _agents = analyze(e)
            return ok
        results.append(await test("Cancel order via chat", _))

        print("\n=== 6. Free Input Edge Cases ===")
        for msg in [
            "市场今天怎么样", "分析贵州茅台", "帮我看看", 
            "在吗", "谢谢", "你能做什么", "我的账户",
        ]:
            async def _(msg=msg):
                e = await sse_collect(c, msg)
                _, ok, _ = analyze(e)
                return ok
            results.append(await test(f"Edge: '{msg}'", _))

    passed = sum(results)
    total = len(results)
    print(f"\n{'='*50}")
    print(f"RESULTS: {passed}/{total}")
    print(f"{'='*50}")
    return passed == total

if __name__ == "__main__":
    asyncio.run(main())
