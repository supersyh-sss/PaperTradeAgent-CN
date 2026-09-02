"""快速全量采集 - 80/batch, 无延迟"""

import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))
os.chdir(str(Path(__file__).parent.parent.parent))

F = "data/a_stock_list.json"


async def fetch(batch):
    import httpx

    url = f"http://qt.gtimg.cn/q={','.join(batch)}"
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.get(url)
            r.encoding = "gbk"
            t = r.text
    except Exception:
        logger.exception("腾讯行情批量请求失败")
        return {}
    res = {}
    for m in re.finditer(r'v_(\w+)="([^"]*)"', t):
        raw, flds = m.group(1), m.group(2).split("~")
        if len(flds) < 2 or not flds[1]:
            continue
        code = raw[2:] if raw[:2] in ("sh", "sz", "bj") else raw
        res[code] = flds[1]
    return res


async def main():
    if os.path.exists(F):
        with open(F, encoding="utf-8") as f:
            d = json.load(f)
        if d.get("date") == datetime.now().strftime("%Y-%m-%d"):
            print(f"OK: {d['count']}只 (cached)")
            return

    # 核心范围 (约5500个有效代码)
    ranges = [
        ("sh", range(600000, 606000)),
        ("sh", range(688000, 690000)),
        ("sz", range(1, 5000)),
        ("sz", range(2000, 4000)),
        ("sz", range(300000, 302000)),
        ("bj", range(430000, 431000)),
        ("bj", range(830000, 832000)),
        ("bj", range(870000, 872000)),
    ]

    all_codes = set()
    for prefix, rng in ranges:
        for ci in rng:
            all_codes.add(f"{prefix}{ci:06d}")

    print(f"Probing {len(all_codes)} codes...")
    codes = list(all_codes)
    B = 80
    found = {}
    t0 = asyncio.get_event_loop().time()

    for i in range(0, len(codes), B):
        batch = codes[i : i + B]
        r = await fetch(batch)
        for c, n in r.items():
            if n and n != c:
                found[c] = n
        if i % (B * 10) == 0:
            elapsed = asyncio.get_event_loop().time() - t0
            print(f"  {i}/{len(codes)} | {len(found)} found | {elapsed:.0f}s")

    # Build
    stocks = []
    for c, n in sorted(found.items()):
        exc = "sh" if c[0] in "65" else "sz" if c[0] in "023" else "bj"
        bd = "主板"
        if c.startswith("68"):
            bd = "科创板"
        elif c.startswith("30"):
            bd = "创业板"
        elif c[0] in "48":
            bd = "北交所"
        elif c.startswith("002"):
            bd = "中小板"
        stocks.append({"code": c, "name": n, "exchange": exc, "board": bd})

    ci, ni = {}, {}
    for s in stocks:
        ci[s["code"]] = {
            "name": s["name"],
            "exchange": s["exchange"],
            "board": s["board"],
        }
        ni.setdefault(s["name"], []).append(s["code"])

    data = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "count": len(stocks),
        "stocks": stocks,
        "code_index": ci,
        "name_index": ni,
        "updated_at": datetime.now().isoformat(),
    }

    os.makedirs("data", exist_ok=True)
    with open(F, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"DONE: {len(stocks)}只 ({os.path.getsize(F) / 1024:.0f}KB)")


asyncio.run(main())
