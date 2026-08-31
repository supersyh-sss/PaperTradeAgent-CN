"""
A股股票代码快速查询服务
数据源: data/a_stock_list.json (5633只全量A股)
特性: 内存加载、代码/名称双向索引、模糊搜索、自动重载
"""

import json
import os

_STOCK_DATA: dict | None = None
_LOAD_TIME: float = 0
_DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "a_stock_list.json")


def _load() -> dict:
    """加载或重载股票列表（自动缓存）"""
    global _STOCK_DATA, _LOAD_TIME
    if _STOCK_DATA is not None and os.path.exists(_DATA_PATH):
        mtime = os.path.getmtime(_DATA_PATH)
        if mtime <= _LOAD_TIME:
            return _STOCK_DATA
    if os.path.exists(_DATA_PATH):
        with open(_DATA_PATH, "r", encoding="utf-8") as f:
            _STOCK_DATA = json.load(f)
        _LOAD_TIME = os.path.getmtime(_DATA_PATH)
        return _STOCK_DATA
    return {"stocks": [], "code_index": {}, "name_index": {}, "count": 0}


def _get() -> dict:
    return _STOCK_DATA if _STOCK_DATA else _load()


def resolve(code_or_name: str) -> dict | None:
    """通过代码或名称解析股票信息
    Args:
        code_or_name: 6位代码(600519) 或 名称(贵州茅台) 或 简称(茅台)
    Returns:
        {"code": "600519", "name": "贵州茅台", "exchange": "sh", "board": "主板"} 或 None
    """
    data = _get()
    ci = data.get("code_index", {})
    
    # 1. 精确代码匹配
    code = code_or_name.strip()
    if code in ci:
        info = ci[code]
        return {"code": code, "name": info["name"], "exchange": info["exchange"], "board": info["board"]}
    
    # 2. 精确名称匹配
    ni = data.get("name_index", {})
    if code in ni:
        codes = ni[code]
        if codes:
            info = ci.get(codes[0], {})
            return {"code": codes[0], "name": code, "exchange": info.get("exchange", ""), "board": info.get("board", "")}
    
    # 3. 模糊名称匹配（子串）
    for name, codes in ni.items():
        if code in name:
            info = ci.get(codes[0], {})
            return {"code": codes[0], "name": name, "exchange": info.get("exchange", ""), "board": info.get("board", "")}
    
    return None


def search(query: str, limit: int = 10) -> list[dict]:
    """模糊搜索股票（代码或名称子串匹配）
    Args:
        query: 搜索关键词
        limit: 返回结果数量上限
    Returns:
        [{"code","name","exchange","board"}, ...]
    """
    data = _get()
    ci = data.get("code_index", {})
    q = query.strip()
    results = []
    seen = set()
    
    # 代码前缀匹配
    for code, info in ci.items():
        if q in code:
            results.append({"code": code, "name": info["name"], "exchange": info["exchange"], "board": info["board"]})
            seen.add(code)
            if len(results) >= limit * 2:
                break
    
    # 名称子串匹配
    ni = data.get("name_index", {})
    for name, codes in ni.items():
        if q in name:
            for code in codes:
                if code not in seen:
                    info = ci.get(code, {})
                    results.append({"code": code, "name": name, "exchange": info.get("exchange", ""), "board": info.get("board", "")})
                    seen.add(code)
                    if len(results) >= limit * 2:
                        break
        if len(results) >= limit * 2:
            break
    
    return results[:limit]


def get_name(code: str) -> str | None:
    """通过代码获取名称"""
    data = _get()
    info = data.get("code_index", {}).get(code.strip())
    return info["name"] if info else None


def get_code(name: str) -> str | None:
    """通过名称获取代码"""
    data = _get()
    codes = data.get("name_index", {}).get(name.strip())
    return codes[0] if codes else None


def get_count() -> int:
    return _get().get("count", 0)


def get_all_stocks() -> list[dict]:
    return _get().get("stocks", [])


def get_date() -> str:
    return _get().get("date", "unknown")


def reload():
    """强制重新加载"""
    global _STOCK_DATA, _LOAD_TIME
    _STOCK_DATA = None
    _LOAD_TIME = 0
    return _load()
