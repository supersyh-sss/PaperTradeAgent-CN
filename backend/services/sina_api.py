"""新浪财经API - 备用数据源"""

import re

import httpx

from ..config import HTTP_TIMEOUT_STOCK, SINA_REALTIME_URL
from ..services.cache import api_limiter


class SinaFinanceAPI:
    """新浪财经实时行情（备用降级）"""

    URL = SINA_REALTIME_URL

    @staticmethod
    def _make_code(code: str) -> str:
        code = code.lower().strip()
        if code.startswith(("sh", "sz", "bj")):
            return code
        if code.startswith(("6", "5")):
            return f"sh{code}"
        if code.startswith(("0", "2", "3")):
            return f"sz{code}"
        if code.startswith(("4", "8")):
            return f"bj{code}"
        return code

    async def get_realtime(self, codes: list[str]) -> dict[str, dict]:
        """获取实时行情（新浪格式）"""
        formatted = [self._make_code(c) for c in codes]
        url = self.URL.format(codes=",".join(formatted))

        async with api_limiter, httpx.AsyncClient(timeout=HTTP_TIMEOUT_STOCK) as client:
            resp = await client.get(url)
            resp.encoding = "gbk"
            text = resp.text

        results = {}
        pattern = re.compile(r'var hq_str_(\w+)="([^"]*)"')
        for match in pattern.finditer(text):
            raw_code = match.group(1)
            fields = match.group(2).split(",")

            if len(fields) < 10:
                continue

            try:
                price = float(fields[3]) if fields[3] else 0
                prev_close = float(fields[2]) if fields[2] else 0
            except (ValueError, IndexError):
                price = 0
                prev_close = 0

            results[raw_code] = {
                "symbol": raw_code,
                "code": raw_code.replace("sh", "").replace("sz", "").replace("bj", ""),
                "name": fields[0],
                "price": price,
                "prev_close": prev_close,
                "open": self._safe_float(fields, 1),
                "high": self._safe_float(fields, 4),
                "low": self._safe_float(fields, 5),
                "volume": self._safe_float(fields, 8) / 100
                if self._safe_float(fields, 8)
                else 0,  # 股->手
                "amount": round(self._safe_float(fields, 9) / 10000, 2)
                if self._safe_float(fields, 9)
                else 0,
                "change": round(price - prev_close, 2) if price else 0,
                "change_pct": round((price / prev_close - 1) * 100, 2)
                if prev_close
                else 0,
                "source": "sina",
            }
        return results

    def _safe_float(self, fields: list[str], idx: int) -> float:
        try:
            return float(fields[idx]) if fields[idx] else 0.0
        except (ValueError, IndexError):
            return 0.0


# 全局实例
sina_api = SinaFinanceAPI()
