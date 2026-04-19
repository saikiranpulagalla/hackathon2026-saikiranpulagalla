"""Comprehensive smoke test — validates all infrastructure without an API key."""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

# Fix Windows console encoding for Unicode output
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

passed = 0
failed = 0


def _pass(name: str):
    global passed
    passed += 1
    print(f"  [PASS] {name}")


def _fail(name: str, error: str):
    global failed
    failed += 1
    print(f"  [FAIL] {name} -- {error}")


async def test_mock_tools():
    """Test 1: All 9 mock tools can be imported and called via retry wrapper."""
    try:
        from src.tools.mock_tools import TOOL_REGISTRY
        from src.tools.retry import retry_with_backoff
        from src.tools.schemas import (
            OrderData, CustomerData, ProductData,
            RefundEligibilityData, RefundResult,
            KnowledgeResult, SendReplyResult, EscalationResult,
        )

        assert len(TOOL_REGISTRY) == 9, f"Expected 9 tools, got {len(TOOL_REGISTRY)}"

        # Call each tool through the retry wrapper (handles simulated failures)
        test_cases = [
            ("get_order", {"order_id": "ORD-TEST"}, OrderData),
            ("get_customer", {"email": "test@example.com"}, CustomerData),
            ("get_customer_orders", {"customer_id": "C001"}, OrderData),
            ("get_product", {"product_id": "PROD-TEST"}, ProductData),
            ("check_refund_eligibility", {"order_id": "ORD-TEST"}, RefundEligibilityData),
            ("issue_refund", {"order_id": "ORD-TEST", "amount": 10.0}, RefundResult),
            ("search_knowledge_base", {"query": "test query"}, KnowledgeResult),
            ("send_reply", {"ticket_id": "TKT-TEST", "message": "Hello"}, SendReplyResult),
            ("escalate", {"ticket_id": "TKT-TEST", "summary": "test", "priority": "medium"}, EscalationResult),
        ]

        callable_count = 0
        for name, args, schema in test_cases:
            result = await retry_with_backoff(
                TOOL_REGISTRY[name], args, schema,
                tool_name=name, max_retries=3, base_delay=0.05,
            )
            # We just verify it returned a ToolResult (success or failure is fine)
            assert hasattr(result, "success"), f"{name} didn't return ToolResult"
            callable_count += 1

        _pass(f"Mock tools importable and callable ({callable_count}/9)")
    except Exception as e:
        _fail("Mock tools importable and callable", str(e))


async def test_retry_wrapper():
    """Test 2: Retry wrapper exhausts retries on transient errors."""
    try:
        from src.tools.retry import retry_with_backoff
        from src.tools.exceptions import ToolTimeoutError
        from src.tools.schemas import OrderData

        call_count = 0

        async def always_timeout(**kwargs):
            nonlocal call_count
            call_count += 1
            raise ToolTimeoutError("simulated timeout")

        result = await retry_with_backoff(
            always_timeout, {}, OrderData,
            tool_name="test_tool", max_retries=3, base_delay=0.01
        )

        assert result.success is False, "Should have failed"
        assert call_count == 3, f"Expected 3 attempts, got {call_count}"
        assert result.error_type == "timeout", f"Expected 'timeout', got '{result.error_type}'"

        _pass("Retry wrapper exhausts 3 attempts")
    except Exception as e:
        _fail("Retry wrapper exhausts 3 attempts", str(e))


async def test_pydantic_schemas():
    """Test 3: Pydantic schemas validate and reject correctly."""
    try:
        from src.tools.schemas import OrderData
        from pydantic import ValidationError

        # Valid dict should pass
        valid = {
            "order_id": "ORD-1001",
            "customer_id": "C001",
            "product_id": "P001",
            "quantity": 1,
            "amount": 129.99,
            "status": "delivered",
            "order_date": "2024-02-10",
        }
        parsed = OrderData.model_validate(valid)
        assert parsed.order_id == "ORD-1001"

        # Malformed dict should fail
        malformed = {"order_id": "ORD-001", "status": None}
        try:
            OrderData.model_validate(malformed)
            assert False, "Should have raised ValidationError"
        except ValidationError:
            pass  # expected

        _pass("Pydantic schemas validate correctly")
    except Exception as e:
        _fail("Pydantic schemas validate correctly", str(e))


async def test_dlq():
    """Test 4: DLQ push/save/load works."""
    try:
        from src.infrastructure.dlq import DeadLetterQueue
        from src.agent.state import DLQEntry
        from datetime import datetime

        dlq = DeadLetterQueue()
        for i in range(3):
            entry = DLQEntry(
                ticket_id=f"TKT-{i:03d}",
                error_type="test_error",
                error_message=f"Test failure {i}",
                timestamp=datetime.now(),
            )
            await dlq.push(entry)

        assert dlq.size == 3, f"Expected 3 entries, got {dlq.size}"

        # Save to temp file and verify
        tmp = os.path.join(tempfile.gettempdir(), "test_dlq.json")
        await dlq.dump(tmp)

        with open(tmp) as f:
            data = json.load(f)
        assert len(data) == 3, f"Expected 3 records in file, got {len(data)}"

        os.unlink(tmp)
        _pass("DLQ push/save/load works")
    except Exception as e:
        _fail("DLQ push/save/load works", str(e))


async def test_router():
    """Test 5: Router confidence thresholds correct."""
    try:
        from src.agent.router import determine_routing

        # Low confidence -> escalate
        r1 = determine_routing(0.45, "auto", "refund_request", [])
        assert r1 == "escalate", f"Expected 'escalate' for conf=0.45, got '{r1}'"

        # High confidence + auto -> auto_resolve
        r2 = determine_routing(0.70, "auto", "refund_request", [])
        assert r2 == "auto_resolve", f"Expected 'auto_resolve' for conf=0.70/auto, got '{r2}'"

        # High confidence + human -> escalate
        r3 = determine_routing(0.70, "human", "complaint", [])
        assert r3 == "escalate", f"Expected 'escalate' for human resolvability, got '{r3}'"

        # At threshold -> auto_resolve
        r4 = determine_routing(0.65, "auto", "order_status", [])
        assert r4 == "auto_resolve", f"Expected 'auto_resolve' at threshold, got '{r4}'"

        _pass("Router confidence thresholds correct")
    except Exception as e:
        _fail("Router confidence thresholds correct", str(e))


async def test_audit_logger():
    """Test 6: AuditLogger records and saves correctly."""
    try:
        from src.infrastructure.audit import AuditLogger
        from src.agent.state import AuditRecord
        from datetime import datetime
        import tempfile

        tmp = os.path.join(tempfile.gettempdir(), "test_audit.json")
        logger = AuditLogger(tmp)

        now = datetime.now()
        record = AuditRecord(
            ticket_id="TKT-SMOKE",
            customer_id="CUST-001",
            customer_email="test@test.com",
            resolution_status="resolved",
            started_at=now,
            completed_at=now,
            node_history=["classifier", "context_fetcher", "router", "resolver", "audit_close"],
            tool_calls=[],
            errors=[],
        )
        await logger.write(record)
        assert logger.count == 1, f"Expected 1 record, got {logger.count}"

        await logger.save()
        with open(tmp) as f:
            data = json.load(f)
        assert len(data) == 1, f"Expected 1 record in file, got {len(data)}"
        assert data[0]["ticket_id"] == "TKT-SMOKE"

        os.unlink(tmp)
        _pass("AuditLogger records tool calls")
    except Exception as e:
        _fail("AuditLogger records tool calls", str(e))


async def run_all():
    print("\nShopWave Support Agent -- Infrastructure Smoke Test")
    print("=" * 55)
    print()

    await test_mock_tools()
    await test_retry_wrapper()
    await test_pydantic_schemas()
    await test_dlq()
    await test_router()
    await test_audit_logger()

    print()
    if failed == 0:
        print(f"  ALL {passed} SMOKE TESTS PASSED -- infrastructure healthy (no API key needed)")
    else:
        print(f"  WARNING: Smoke test failed. {failed} test(s) need fixing. Fix infrastructure before running full agent.")
    print()


asyncio.run(run_all())
