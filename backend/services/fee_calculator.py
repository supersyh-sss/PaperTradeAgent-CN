"""
A股交易费用计算器
规则基于中国大陆券商标准（2026年最新）：
- 印花税: 卖出时按成交金额的 0.05% 单边收取 (2023年8月减半后)
- 交易佣金: 买卖双向收取，默认万2.5 (0.025%)，最低5元
- 过户费: 买卖双向收取，万0.1 (0.001%)，仅沪市
- 规费: 含经手费+证管费，买卖双向，万0.641 (实际上大多经纪商含在佣金内)
"""

from typing import Dict, Optional, Tuple


# A股费用标准（可配置）
STAMP_TAX_RATE = 0.0005        # 印花税：卖出 0.05%
COMMISSION_RATE = 0.00025      # 佣金：万2.5
COMMISSION_MIN = 5.0           # 最低佣金 5 元
TRANSFER_FEE_RATE = 0.00001    # 过户费：万0.1（仅沪市）


def calculate_fee(
    amount: float,
    side: str,
    exchange: str = "sh",
    commission_rate: float = COMMISSION_RATE,
) -> Tuple[float, Dict[str, float]]:
    """计算单笔交易的费用

    Args:
        amount: 成交金额 (价格 × 数量)
        side: "BUY" 或 "SELL"
        exchange: "sh" / "sz" / "bj"
        commission_rate: 佣金费率（默认万2.5）

    Returns:
        (total_fee, fee_breakdown)
    """
    breakdown = {}

    # 1. 佣金（双向）
    commission = amount * commission_rate
    if commission < COMMISSION_MIN:
        commission = COMMISSION_MIN
    breakdown["佣金"] = round(commission, 2)

    # 2. 印花税（仅卖出）
    stamp_tax = amount * STAMP_TAX_RATE if side.upper() == "SELL" else 0.0
    breakdown["印花税"] = round(stamp_tax, 2)

    # 3. 过户费（仅沪市买卖双向）
    transfer_fee = amount * TRANSFER_FEE_RATE if exchange == "sh" else 0.0
    breakdown["过户费"] = round(transfer_fee, 2)

    total = commission + stamp_tax + transfer_fee
    breakdown["合计"] = round(total, 2)

    return round(total, 2), breakdown


def calculate_net_amount(
    price: float,
    quantity: int,
    side: str,
    exchange: str = "sh",
) -> Tuple[float, float]:
    """计算成交净额和费用

    Returns:
        (net_amount, total_fee)
        买入: net_amount = -(amount + fee) （扣款为正数）
        卖出: net_amount = +(amount - fee) （到账为正数）
    """
    gross = price * quantity
    fee, _ = calculate_fee(gross, side, exchange)
    if side.upper() == "BUY":
        return -(gross + fee), fee
    else:
        return gross - fee, fee


def format_fee_estimate(price: float, quantity: int, side: str, exchange: str = "sh") -> str:
    """生成费用预估说明文本"""
    gross = price * quantity
    fee, breakdown = calculate_fee(gross, side, exchange)
    lines = [
        f"预估成交金额: {gross:,.2f} 元",
        f"交易费用合计: {fee:,.2f} 元",
    ]
    if side.upper() == "SELL":
        net = gross - fee
        lines.append(f"预估到账净额: {net:,.2f} 元")
    else:
        net = gross + fee
        lines.append(f"预估实际支出: {net:,.2f} 元")
    lines.append(f"\n费用明细:")
    for name, val in breakdown.items():
        if val > 0:
            lines.append(f"  - {name}: {val:.2f} 元")
    return "\n".join(lines)
