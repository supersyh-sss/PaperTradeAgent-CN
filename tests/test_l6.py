"""L6 人机协同设计单元测试 — 最小权限 / 可感知降级 / 低置信度求助（确定性，无网络/API）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.agents.response_generator import (
    _DEGRADED_PREFIX,
    _LOW_CONFIDENCE_NOTE,
    _fallback_response,
    _fallback_response_body,
    _low_confidence_note,
)
from backend.harness.safety_gate import PermissionLevel, SafetyGate


# ---- D1 最小权限默认值 ----
def test_default_permission_is_read_only():
    gate = SafetyGate()
    assert gate.get_user_permission("default") == PermissionLevel.READ_ONLY


def test_read_operation_allowed_by_default():
    gate = SafetyGate()
    assert gate.check_operation("query", "default").allowed is True


def test_write_operation_requires_explicit_authorization():
    gate = SafetyGate()
    denied = gate.check_operation("trade", "default")
    assert denied.allowed is False
    granted = gate.check_operation("trade", "default", authorized=True)
    assert granted.allowed is True


def test_write_operation_whitelist():
    assert "trade" in SafetyGate.WRITE_OPERATIONS
    assert "cancel_order" in SafetyGate.WRITE_OPERATIONS
    assert "watchlist_add" in SafetyGate.WRITE_OPERATIONS
    assert "query" not in SafetyGate.WRITE_OPERATIONS


# ---- D2 可感知优雅降级 ----
def test_fallback_response_has_degraded_prefix():
    state = {"intent": "chat"}
    resp = _fallback_response(state)
    assert resp.startswith(_DEGRADED_PREFIX)
    assert _fallback_response_body(state) in resp


# ---- D3 低置信度主动求助 ----
def test_low_confidence_note_triggered_by_weak_quant():
    state = {"quant_assessment": {"strength_rating": 2}}
    assert _low_confidence_note(state) == _LOW_CONFIDENCE_NOTE


def test_low_confidence_note_triggered_by_neutral_sentiment():
    state = {"intelligence_assessment": {"sentiment_score": 0.05}}
    assert _low_confidence_note(state) == _LOW_CONFIDENCE_NOTE


def test_low_confidence_note_empty_when_confident():
    state = {
        "quant_assessment": {
            "strength_rating": 8,
            "volatility_assessment": "moderate",
            "risk_flags": [],
        },
        "intelligence_assessment": {
            "sentiment_score": 0.8,
            "impact_strength": "strong",
        },
    }
    assert _low_confidence_note(state) == ""
