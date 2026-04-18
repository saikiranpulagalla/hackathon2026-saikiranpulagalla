"""Unit tests for the retry wrapper."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.tools.exceptions import ToolError, ToolTimeoutError
from src.tools.retry import retry_with_backoff
from src.tools.schemas import OrderData


@pytest.mark.asyncio
async def test_success_on_first_attempt():
    async def good_tool(**kwargs):
        return {
            "order_id": "ORD-001", "customer_id": "CUST-001",
            "status": "shipped", "items": [], "total_amount": 50.0,
            "created_at": "2024-01-01T00:00:00",
        }

    result = await retry_with_backoff(good_tool, {"order_id": "ORD-001"}, OrderData, tool_name="get_order", base_delay=0.01)
    assert result.success is True
    assert result.attempt == 1


@pytest.mark.asyncio
async def test_retries_on_timeout_then_succeeds():
    call_count = 0

    async def flaky_tool(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise ToolTimeoutError("timeout")
        return {
            "order_id": "ORD-001", "customer_id": "CUST-001",
            "status": "shipped", "items": [], "total_amount": 50.0,
            "created_at": "2024-01-01T00:00:00",
        }

    result = await retry_with_backoff(flaky_tool, {}, OrderData, tool_name="get_order", max_retries=3, base_delay=0.01)
    assert result.success is True
    assert result.attempt == 2
    assert call_count == 2


@pytest.mark.asyncio
async def test_no_retry_on_validation_error():
    call_count = 0

    async def bad_tool(**kwargs):
        nonlocal call_count
        call_count += 1
        return {"order_id": "ORD-001", "status": None, "items": "INVALID"}  # malformed

    result = await retry_with_backoff(bad_tool, {}, OrderData, tool_name="get_order", max_retries=3, base_delay=0.01)
    assert result.success is False
    assert result.error_type == "validation"
    assert call_count == 1  # no retry on validation failure


@pytest.mark.asyncio
async def test_all_retries_exhausted():
    async def always_fails(**kwargs):
        raise ToolTimeoutError("always timeout")

    result = await retry_with_backoff(always_fails, {}, OrderData, tool_name="get_order", max_retries=3, base_delay=0.01)
    assert result.success is False
    assert result.error_type == "timeout"
    assert result.attempt == 3


@pytest.mark.asyncio
async def test_non_transient_error_stops_immediately():
    call_count = 0

    async def permanent_error(**kwargs):
        nonlocal call_count
        call_count += 1
        raise ToolError("permanent failure", is_transient=False)

    result = await retry_with_backoff(permanent_error, {}, OrderData, tool_name="get_order", max_retries=3, base_delay=0.01)
    assert result.success is False
    assert call_count == 1  # stopped immediately


@pytest.mark.asyncio
async def test_result_never_raises():
    """retry_with_backoff must always return ToolResult, never raise."""
    async def crash(**kwargs):
        raise RuntimeError("unexpected crash")

    result = await retry_with_backoff(crash, {}, OrderData, tool_name="get_order", max_retries=2, base_delay=0.01)
    assert result.success is False
