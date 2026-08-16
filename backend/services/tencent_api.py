"""腾讯财经API - 主数据源"""
import httpx
import re
import json
import logging
from typing import Dict, List, Optional
from ..services.cache import api_limiter
from ..services.symbol import to_tencent_code, pure_code
from ..config import HTTP_TIMEOUT_STOCK, TENCENT_REALTIME_URL, TENCENT_KLINE_URL

logger = logging.getLogger(__name__)


class TencentFinanceAPI:
    """腾讯财经实时行情 + 历史K线"""

    REALTIME_URL = TENCENT_REALTIME_URL
    KLINE_URL = TENCENT_KLINE_URL

    # 腾讯行情返回字段索引（~分隔）
    # 0:未知 1:名称 2:代码 3:最新价 4:昨收 5:今开 6:成交量(手)
    # 7:外盘 8:内盘 9:买一价 10:买一量 ... 33:最高价 34:最低价
    # 36:成交量 37:成交额(万) 38:换手率 39:市盈率 44:流通市值 45:总市值 46:市净率
    FIELD_MAP = {
        "name": 1, "code": 2, "price": 3, "prev_close": 4,
        "open": 5, "volume": 6, "high": 33, "low": 34,
        "amount": 37, "turnover": 38, "pe": 39, "pb": 46,
    }

    @staticmethod
    def _make_code(code: str) -> str:
        """统一代码格式：sh600519 或 sz000001（保持向后兼容）"""
        return to_tencent_code(code)

    async def get_realtime(self, codes: List[str]) -> Dict[str, dict]:
        """获取实时行情"""
        formatted = [self._make_code(c) for c in codes]
        url = self.REALTIME_URL.format(codes=",".join(formatted))

        async with api_limiter:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_STOCK) as client:
                resp = await client.get(url)
                resp.encoding = "gbk"
                text = resp.text

        results = {}
        pattern = re.compile(r'v_(\w+)="([^"]*)"')
        for match in pattern.finditer(text):
            raw_code = match.group(1)
            fields = match.group(2).split("~")

            # 字段不足或返回为空行情时跳过，避免 IndexError
            if len(fields) < max(self.FIELD_MAP.values()) + 1 or not fields[self.FIELD_MAP["name"]]:
                logger.warning(f"腾讯行情返回异常或空数据: {raw_code}")
                continue

            clean_code = pure_code(raw_code)
            try:
                price = float(fields[self.FIELD_MAP["price"]]) if fields[self.FIELD_MAP["price"]] else 0
            except (ValueError, IndexError):
                price = 0

            prev_close = self._safe_float(fields, "prev_close")
            results[clean_code] = {
                "symbol": clean_code,
                "code": clean_code,
                "name": fields[self.FIELD_MAP["name"]],
                "price": price,
                "prev_close": prev_close,
                "open": self._safe_float(fields, "open"),
                "high": self._safe_float(fields, "high"),
                "low": self._safe_float(fields, "low"),
                "volume": self._safe_float(fields, "volume"),
                "amount": self._safe_float(fields, "amount"),
                "turnover": self._safe_float(fields, "turnover"),
                "pe": self._safe_float(fields, "pe"),
                "pb": self._safe_float(fields, "pb"),
                "change": round(price - prev_close, 2) if price else 0,
                "change_pct": round((price / prev_close - 1) * 100, 2) if prev_close else 0,
                "source": "tencent",
            }
        return results

    async def get_kline(self, code: str, period: str = "day", count: int = 250) -> Optional[List[dict]]:
        """获取历史K线数据"""
        formatted = self._make_code(code)
        params = {
            "_var": f"kline_{period}qfq",
            "param": f"{formatted},{period},,,{count},qfq",
            "r": "0.123456789",
        }
        async with api_limiter:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_STOCK) as client:
                resp = await client.get(self.KLINE_URL, params=params)
                text = resp.text

        # 提取 JSON 数据
        try:
            start = text.index("{")
            end = text.rindex("}") + 1
            data = json.loads(text[start:end])
        except (ValueError, json.JSONDecodeError):
            return None

        stock_data = data.get("data", {}).get(formatted, {})
        kline_list = stock_data.get(period, []) if period in stock_data else stock_data.get(f"qfq{period}", [])

        if not kline_list:
            return None

        return [
            {
                "date": row[0],
                "open": float(row[1]),
                "close": float(row[2]),
                "high": float(row[3]),
                "low": float(row[4]),
                "volume": float(row[5]) if len(row) > 5 else 0,
            }
            for row in kline_list
        ]

    def _safe_float(self, fields: List[str], key: str) -> float:
        try:
            val = fields[self.FIELD_MAP[key]]
            return float(val) if val else 0.0
        except (ValueError, IndexError):
            return 0.0


# 全局实例
tencent_api = TencentFinanceAPI()
