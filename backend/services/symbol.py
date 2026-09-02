"""股票代码规范化工具

原则：
- 数据库、业务层统一使用 6 位数字代码（如 600519）
- 调用外部行情 API 时才拼接交易所前缀（sh/sz/bj）
- 所有入口都接受 sh600519 / 600519 / 贵州茅台 等形态，统一归一化
- 代码/名称映射优先使用 stock_lookup 全量数据（5633只）
"""

import re

_CODE_RE = re.compile(r"(?:sh|sz|bj)?(\d{6})")


def exchange_prefix(symbol: str) -> str:
    """返回交易所前缀 sh / sz / bj"""
    symbol = pure_code(symbol)
    c = symbol[0]
    if c == "6":
        return "sh"
    if c in "03":
        return "sz"
    if c in "48":
        return "bj"
    return "sz"


def is_stock_code(symbol: str) -> bool:
    """判断是否为真实股票代码（非指数）。
    股票代码特征：0/3/6/4/8开头6位数字
    指数代码（如sh000001, sz399001）在规范化后虽然也是6位，但含义不同，
    本函数通过前缀判断：指数如000001/399001不是常规股票代码。
    """
    s = pure_code(symbol)
    if len(s) != 6 or not s.isdigit():
        return False
    return s[0] in "03648"


def normalize_symbol(query: str) -> str | None:
    """将任意输入归一化为 6 位数字代码；无法识别返回 None"""
    if not query:
        return None
    query = query.strip()

    # 1. 直接匹配 6 位数字（支持 sh600519 / sz000001 / 600519）
    match = _CODE_RE.search(query)
    if match:
        code = match.group(1)
        if code.isdigit() and len(code) == 6:
            return code

    # 2. 使用全量 stock_lookup 做名称→代码转换
    try:
        from .stock_lookup import resolve

        result = resolve(query)
        if result:
            return result["code"]
    except Exception:
        pass

    return None


def to_tencent_code(code: str) -> str:
    """转换为腾讯财经接口格式 sh600519 / sz000001 / bj430047。
    若输入已带交易所前缀，则直接透传（用于指数等场景）。
    """
    c = (code or "").strip().lower()
    if len(c) == 8 and c.startswith(("sh", "sz", "bj")) and c[2:].isdigit():
        return c
    norm = normalize_symbol(code)
    if not norm:
        return code
    return f"{exchange_prefix(norm)}{norm}"


def pure_code(code: str) -> str:
    """去掉交易所前缀，返回 6 位数字代码；若无法识别原样返回"""
    norm = normalize_symbol(code)
    return norm if norm else code
