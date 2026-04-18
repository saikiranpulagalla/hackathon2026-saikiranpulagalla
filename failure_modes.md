# Failure Modes Analysis — ShopWave Support Agent

This document describes how the system handles each class of failure. Every scenario has been tested against the mock tool failure rates configured in `src/tools/mock_tools.py`.

---

## Scenario 1: Tool Timeout During Refund Processing

**What happens:** `check_refund_eligibility()` or `issue_refund()` raises `ToolTimeoutError` mid-refund workflow.

**Impact:** Agent cannot confirm eligibility or issue the refund.

**System response:**
- `retry_with_backoff()` catches `ToolTimeoutError`, marks it `recoverable=True`
- Retries up to 3 times with exponential backoff: 0.5s → 1.0s → 2.0s + jitter
- Each attempt is logged as a `ToolCallRecord` with `error_type="timeout"`
- After 3 failures: returns `ToolResult(success=False)`
- Resolver catches the failure and escalates with reason "Could not verify refund eligibility"
- Customer receives acknowledgment: "Your request has been escalated to our specialist team"

**DLQ:** Not triggered — escalation is a valid terminal state.

**Audit log entry:** Shows 3 timeout attempts, escalation reason, and full context gathered before failure.

---

## Scenario 2: Malformed Tool Output (Schema Validation Failure)

**What happens:** `get_order()` returns `{"order_id": "ORD-123", "status": None, "items": "INVALID"}` — a dict with wrong/missing fields.

**Impact:** Invalid data would corrupt agent reasoning if it entered state unchecked.

**System response:**
- Tool returns normally (HTTP 200 equivalent — no exception raised)
- `retry_with_backoff()` calls `schema.model_validate(raw_response)`
- Pydantic raises `ValidationError` — `status: None` fails the `Literal` constraint
- Wrapper catches `ValidationError`, marks it `recoverable=False` (same args → same bad response)
- Does **not** retry — stops immediately
- Returns `ToolResult(success=False, error_type="validation")`
- Context fetcher stores `order_data=None` and logs an `ErrorRecord`
- Downstream resolver handles missing order data gracefully

**Root cause logged:** `audit_log.json` contains the exact validation error message.

---

## Scenario 3: Refund Issued But Reply Fails

**What happens:** `issue_refund()` succeeds and returns a `refund_id`, but `send_reply()` subsequently fails on all 3 retry attempts.

**Impact:** Customer never notified of successful refund.

**System response:**
- `refund_result` is stored in state immediately after `issue_refund()` succeeds (idempotency guard)
- `send_reply()` failure triggers 3 retries with backoff
- After 3 failures: `ToolResult(success=False)` returned
- Resolver logs `ErrorRecord` for the reply failure
- Ticket is escalated with flag `"refund_issued_reply_pending"` in escalation summary
- Human agent sees: refund was issued (with `refund_id`), customer not yet notified

**No double refund:** The idempotency guard (`state.refund_result`) prevents `issue_refund()` from being called again on any retry path.

---

## Scenario 4: Classification Confidence Too Low

**What happens:** Ambiguous ticket text produces `confidence=0.42` from Groq Llama 3.3.

**Impact:** Wrong automated action could harm customer (e.g., wrong refund amount).

**System response:**
- Classifier returns `confidence=0.42`, `resolvability="auto"`
- Router evaluates: `0.42 < 0.65` → `routing_decision = "escalate"`
- Resolver takes escalation path immediately — no tool calls attempted on the ticket content
- Escalation summary includes: low confidence reason, both possible interpretations, classification reasoning
- Human agent receives full context to resolve correctly

**Design principle:** "When uncertain, do nothing and ask" — better to escalate than to act wrongly on an irreversible action.

---

## Scenario 5: All Tools Fail (Cascade Failure)

**What happens:** External service outage causes 100% tool failure rate across all 8 tools.

**Impact:** No tickets can be auto-resolved.

**System response:**
- Context fetcher: `get_customer()` and `get_order()` both fail after 3 retries each
- Router: detects errors but they are `recoverable=True` (timeouts) → routes to escalate, not DLQ
- Resolver escalation path: `escalate()` also fails after 3 retries
- `resolution_status = "failed"` — ticket goes to DLQ
- `audit_close_node` writes DLQ entry with full partial state
- Customer receives no reply (send_reply also failed)
- `dlq.json` preserves all failed tickets for human review

**Graceful degradation:** System processes all 20 tickets, all go to DLQ. No crashes. Operators can review `dlq.json` and process manually.

---

## Scenario 6: Resolver Max Iterations Exceeded

**What happens:** The LLM tool-use loop runs for 5 iterations without reaching `finish_reason = "stop"` (e.g., the model keeps requesting tools without converging).

**Impact:** Ticket processing stalls and consumes API credits.

**System response:**
- Resolver exits the loop after iteration 5
- Sets `resolution_status = "failed"`
- Appends `ErrorRecord(error_type="max_retries", message="Max resolver iterations exceeded")`
- `audit_close_node` routes ticket to DLQ
- All tool calls made during the loop are preserved in `tool_calls` for debugging

**Prevention:** The resolver prompt explicitly instructs the LLM to end with `send_reply` and limits context to avoid runaway loops.

---

## Scenario 7: Concurrent Exception in asyncio.gather

**What happens:** An unhandled exception escapes a ticket's LangGraph workflow coroutine entirely (e.g., network error during Groq API call that bypasses all error handling).

**Impact:** One ticket's processing crashes completely.

**System response:**
- `process_ticket()` wraps `workflow.ainvoke()` in a `try/except Exception`
- Caught exception is passed to `dlq.push_from_exception()`
- DLQ entry created with `error_type="agent_crash"` and the exception message
- `asyncio.gather()` continues processing remaining tickets unaffected
- Final summary shows the ticket as "failed" with DLQ entry

**Isolation:** Each ticket runs in its own coroutine. One crash cannot affect other tickets in flight.
