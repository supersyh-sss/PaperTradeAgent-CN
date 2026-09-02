"""Harness Engineering Framework — 功能测试"""

import os
import sys

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def test_context_manager():
    """Test 1: ContextManager with priority-based token budget"""
    from backend.harness.context_manager import ContextManager

    cm = ContextManager()
    cm.inject_system_critical("SYSTEM: You are a helpful assistant.")
    cm.inject_user_intent("分析茅台走势", "2026-08-12 14:30")
    cm.inject_history(
        "Previous conversation about stocks",
        [{"role": "user", "content": "查看自选股"}],
    )
    cm.inject_agent_output("quant", {"trend": "bullish", "score": 7.5}, "量化评估")
    ctx = cm.build_context()
    assert "SYSTEM" in ctx, "System critical missing"
    assert "分析茅台" in ctx, "User intent missing"
    assert "bullish" in ctx, "Agent output missing"
    print(
        f"[PASS] ContextManager: built context with {len(ctx)} chars (~{len(ctx) // 2} tokens)"
    )


def test_fallback_chain():
    """Test 2: FallbackChain — multi-level JSON parse recovery"""
    from backend.harness.call_interceptor import FallbackChain

    # Level 1: JSON in markdown fence
    broken = '```json\n{"intent": "analyze", "symbol": "sh600519"}\n```'
    result, method, _errors = FallbackChain.parse(broken)
    assert result.get("intent") == "analyze", f"Expected analyze, got {result}"
    assert method == "json_parse", f"Expected json_parse, got {method}"
    print(f"[PASS] FallbackChain: markdown fence [{method}]")

    # Level 2: Regex extraction from mixed text
    raw_text = 'The intent is analyze for stock sh600519. Output: {"intent": "analyze", "stock_symbol": "sh600519", "needs_report": true}'
    result, method, _errors = FallbackChain.parse(raw_text)
    assert result.get("intent") == "analyze", f"Expected analyze, got {result}"
    assert method in ("regex_extract", "line_parse", "json_parse"), (
        f"Unexpected method: {method}"
    )
    print(f"[PASS] FallbackChain: mixed text extraction [{method}]")

    # Level 3: Line parse fallback
    line_text = "intent: analyze\nstock_symbol: sh600519\nneeds_report: true"
    result, method, _errors = FallbackChain.parse(line_text)
    assert result.get("intent") == "analyze", f"Expected analyze, got {result}"
    print(f"[PASS] FallbackChain: line parse [{method}]")


def test_safety_gate():
    """Test 3: SafetyGate — injection detection and sanitization"""
    from backend.harness.safety_gate import SafetyGate

    sg = SafetyGate()

    # SQL injection
    clean, issues = sg.sanitize_input("分析茅台; DROP TABLE users;")
    assert "FILTERED" in clean, f"Expected FILTERED in: {clean}"
    assert len(issues) > 0, "Expected issues detected"
    print(f"[PASS] SafetyGate: SQL injection detected ({len(issues)} issues)")

    # XSS injection
    clean, issues = sg.sanitize_input("<script>alert('xss')</script>")
    assert "FILTERED" in clean, f"Expected FILTERED in: {clean}"
    print(f"[PASS] SafetyGate: XSS injection detected ({len(issues)} issues)")

    # Clean input
    clean, issues = sg.sanitize_input("分析茅台走势怎么样")
    assert "FILTERED" not in clean, "Clean input should not be filtered"
    assert len(issues) == 0, "No issues expected"
    print("[PASS] SafetyGate: clean input passes")


def test_metrics_collector():
    """Test 4: MetricsCollector — session tracking"""
    from backend.harness.metrics import MetricsCollector, MetricType

    mc = MetricsCollector()
    mc.start_session("test_session_001")
    mc.record(MetricType.LLM_CALL_COUNT, 3)
    mc.record(MetricType.TOKEN_USAGE, 5000)
    mc.record(MetricType.TASK_SUCCESS, 1)
    mc.record(MetricType.TOOL_CALL_FALLBACK, 1)
    mc.record(MetricType.LLM_CALL_ERROR, 1)
    metrics = mc.end_session()
    assert metrics is not None
    assert metrics.success_rate == 1.0
    d = metrics.to_dict()
    assert d["llm_calls"]["total"] == 3
    assert d["llm_calls"]["errors"] == 1
    assert d["tokens"] == 5000
    print(f"[PASS] MetricsCollector: {d}")


def test_circuit_breaker():
    """Test 5: CircuitBreaker — state transitions"""
    from backend.harness.resilience import CircuitBreaker

    cb = CircuitBreaker(name="test_breaker", failure_threshold=5)
    assert cb.state.value == "closed"

    for i in range(5):
        cb.record_failure()
    assert cb.state.value == "open", f"Expected open after 5 failures, got {cb.state}"
    assert not cb.allow_request(), "Should reject in OPEN state"
    print("[PASS] CircuitBreaker: OPEN after 5 failures, requests rejected")


def test_contract_registry():
    """Test 6: ContractRegistry — schema validation"""
    from backend.harness.contracts import ContractRegistry

    cr = ContractRegistry()

    # Valid output
    valid = {"intent": "analyze", "needed_agents": [], "needs_report": True}
    _validated, issues = cr.validate("chief_strategist", valid)
    assert len(issues) == 0, f"Unexpected issues: {issues}"
    print("[PASS] ContractRegistry: valid output accepted")

    # Invalid: missing required field
    invalid = {"intent": "unknown"}
    _validated, issues = cr.validate("chief_strategist", invalid)
    assert len(issues) > 0, "Should detect missing required fields"
    print(f"[PASS] ContractRegistry: invalid output rejected ({len(issues)} issues)")


def test_state_manager():
    """Test 7: StateManager — checkpoint and rollback"""
    from backend.harness.state_manager import StateManager

    sm = StateManager()
    sm.init_session("sess_001", {"intent": "chat", "count": 0})
    sm.set("sess_001", "count", 5)
    sm.checkpoint("sess_001", "test_agent", "before")
    sm.set("sess_001", "count", 10)
    sm.checkpoint("sess_001", "test_agent", "after", {"delta": 5})

    # Rollback to first checkpoint
    cks = sm._checkpoints.get("sess_001", [])
    assert len(cks) >= 2, f"Expected 2 checkpoints, got {len(cks)}"

    sm.rollback("sess_001", cks[0].checkpoint_id)
    assert sm.get("sess_001", "count") == 5, "Rollback should restore count to 5"
    print(f"[PASS] StateManager: checkpoint & rollback ({len(cks)} checkpoints)")


def test_sandbox_manager():
    """Test 8: SandboxManager — isolation levels"""
    from backend.harness.sandbox import IsolationLevel, SandboxManager

    sm = SandboxManager(current_level=IsolationLevel.PROCESS)
    assert sm.can_execute("execute_trade")
    assert sm.can_execute("network_access")
    assert not sm.can_execute("modify_account"), "Need CONTAINER for account modify"
    print("[PASS] SandboxManager: isolation gating works")


def test_feedback_assembler():
    """Test 9: FeedbackAssembler — structured feedback"""
    from backend.harness.feedback_assembler import FeedbackAssembler

    fb = FeedbackAssembler.success(
        "quant_researcher",
        "execution",
        "Analysis complete",
        {"trend": "bullish"},
        {"tokens": 500},
    )
    assert "bullish" in fb.to_injectable()
    print("[PASS] FeedbackAssembler: success feedback generated")

    fb = FeedbackAssembler.error(
        "trade_executor",
        "execution",
        errors=["Price out of range"],
        suggestions=["Retry with adjusted price"],
    )
    assert "Price out of range" in fb.to_injectable()
    print("[PASS] FeedbackAssembler: error feedback generated")


if __name__ == "__main__":
    tests = [
        ("ContextManager", test_context_manager),
        ("FallbackChain", test_fallback_chain),
        ("SafetyGate", test_safety_gate),
        ("MetricsCollector", test_metrics_collector),
        ("CircuitBreaker", test_circuit_breaker),
        ("ContractRegistry", test_contract_registry),
        ("StateManager", test_state_manager),
        ("SandboxManager", test_sandbox_manager),
        ("FeedbackAssembler", test_feedback_assembler),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except Exception as e:
            print(f"[FAIL] {name}: {e}")
            failed += 1

    print()
    print(f"{'=' * 50}")
    print(f"RESULTS: {passed} passed, {failed} failed out of {len(tests)}")
    print(f"{'=' * 50}")

    if failed > 0:
        sys.exit(1)
