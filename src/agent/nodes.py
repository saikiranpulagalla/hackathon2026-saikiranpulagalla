"""All 5 LangGraph node functions for the ShopWave Support Agent."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime
from typing import Any

from openai import AsyncOpenAI

from ..tools.mock_tools import TOOL_REGISTRY
from ..tools.retry import ToolResult, retry_with_backoff
from ..tools.schemas import SCHEMA_REGISTRY
from .router import URGENCY_TO_PRIORITY_MAP, determine_routing
from .state import AuditRecord, DLQEntry, ErrorRecord, ToolCallRecord

logger = logging.getLogger(__name__)

# --- Groq Client (lazy init) - uses OpenAI SDK ---
_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY environment variable not set")
        _client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://api.groq.com/openai/v1",
        )
    return _client


# --- Classification Prompt ---

CLASSIFICATION_PROMPT_TEMPLATE = """You are a customer support ticket classifier for ShopWave, an e-commerce platform.

Classify the following support ticket:

Ticket:
{ticket_text}

Customer ID: {customer_id}
Order ID: {order_id}

Classify the intent into one of:
- refund_request: Customer wants a refund or return
- order_status: Customer asking about order location or delivery
- product_question: Customer asking about a product's features, availability, or specs
- billing_issue: Customer has a billing or payment problem
- account_issue: Customer has an account access or settings problem
- complaint: General complaint not fitting other categories
- other: Does not fit any category above

Respond with ONLY valid JSON in this exact format:
{{
  "intent": "<intent_name>",
  "urgency": "high|medium|low",
  "resolvability": "auto|human",
  "confidence": <float between 0.0 and 1.0>,
  "reasoning": "<one sentence explanation>"
}}

Guidelines:
- urgency "high": legal threats, payment failures, account locked, VIP customer issues
- urgency "medium": refund requests, shipping delays, product defects
- urgency "low": general questions, product inquiries, feedback
- resolvability "human": complex disputes, legal threats, ambiguous situations, confidence < 0.6
- resolvability "auto": clear-cut requests with sufficient information to act"""


# --- Tool Definitions for Groq (OpenAI format) ---

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "check_refund_eligibility",
            "description": "Check if an order is eligible for a refund. Call this before issuing any refund.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "description": "The order ID to check"},
                },
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "issue_refund",
            "description": "Issue a refund for an order. IRREVERSIBLE. Only call after confirming eligibility.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "description": "The order ID to refund"},
                    "amount": {"type": "number", "description": "The refund amount"},
                },
                "required": ["order_id", "amount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_order",
            "description": "Fetch order details including status, items, and tracking.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "description": "The order ID"},
                },
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product",
            "description": "Fetch product details including price, stock status, and specs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "string", "description": "The product ID"},
                },
                "required": ["product_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_knowledge_base",
            "description": "Search the knowledge base for relevant articles and policies.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_reply",
            "description": "Send a reply message to the customer for the current ticket.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticket_id": {"type": "string", "description": "The ticket ID"},
                    "message": {"type": "string", "description": "The reply message to send"},
                },
                "required": ["ticket_id", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate",
            "description": "Escalate the ticket to a specialist human agent team.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticket_id": {"type": "string", "description": "The ticket ID"},
                    "summary": {"type": "string", "description": "Summary of the issue and actions taken"},
                    "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"]},
                },
                "required": ["ticket_id", "summary", "priority"],
            },
        },
    },
]


def _get_tools_for_intent(intent: str) -> list[dict]:
    """Return the relevant tool definitions based on ticket intent."""
    intent_tools = {
        "refund_request": ["check_refund_eligibility", "issue_refund", "get_order", "search_knowledge_base", "send_reply"],
        "order_status": ["get_order", "search_knowledge_base", "send_reply"],
        "product_question": ["get_product", "search_knowledge_base", "send_reply"],
        "billing_issue": ["get_order", "search_knowledge_base", "send_reply"],
        "account_issue": ["search_knowledge_base", "escalate", "send_reply"],
        "complaint": ["search_knowledge_base", "escalate", "send_reply"],
        "other": ["search_knowledge_base", "escalate", "send_reply"],
    }
    allowed = intent_tools.get(intent, ["search_knowledge_base", "escalate", "send_reply"])
    return [t for t in TOOL_DEFINITIONS if t["function"]["name"] in allowed]


def _make_tool_call_record(result: ToolResult, args: dict) -> ToolCallRecord:
    """Convert a ToolResult into a ToolCallRecord for audit."""
    return ToolCallRecord(
        tool_name=result.tool_name,
        attempt=result.attempt,
        timestamp=datetime.now(),
        input_args=args,
        success=result.success,
        error_type=result.error_type,
        duration_ms=result.duration_ms,
        validated=result.success,
    )


def _make_error_record(result: ToolResult, node: str) -> ErrorRecord:
    """Convert a failed ToolResult into an ErrorRecord."""
    return ErrorRecord(
        node=node,
        tool_name=result.tool_name,
        error_type=result.error_type or "unknown",
        message=result.error_message or "",
        recoverable=result.recoverable if result.recoverable is not None else True,
    )


# ============================================================
# Node 1: Classifier
# ============================================================

async def classifier_node(state: dict) -> dict:
    """
    Classify ticket intent, urgency, resolvability, and confidence using Groq LLM.
    On parse failure: defaults to high urgency, human resolvability, 0.0 confidence.
    """
    prompt = CLASSIFICATION_PROMPT_TEMPLATE.format(
        ticket_text=state["ticket_text"],
        customer_id=state["customer_id"],
        order_id=state.get("order_id", "N/A"),
    )

    try:
        client = _get_client()
        response = await client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
        )

        text = response.choices[0].message.content.strip()

        # Extract JSON from response (handle markdown code blocks)
        json_match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group())
        else:
            parsed = json.loads(text)

        # Validate required fields
        intent = parsed.get("intent", "unknown")
        urgency = parsed.get("urgency", "medium")
        resolvability = parsed.get("resolvability", "auto")
        confidence = float(parsed.get("confidence", 0.0))
        reasoning = parsed.get("reasoning", "")

        # Clamp confidence
        confidence = max(0.0, min(1.0, confidence))

        # Validate enum values
        if urgency not in ("high", "medium", "low"):
            urgency = "medium"
        if resolvability not in ("auto", "human"):
            resolvability = "auto"

        logger.info(
            f"[{state['ticket_id']}] Classified: intent={intent}, "
            f"urgency={urgency}, resolvability={resolvability}, confidence={confidence:.2f}"
        )

        return {
            "intent": intent,
            "urgency": urgency,
            "resolvability": resolvability,
            "confidence": confidence,
            "classification_reasoning": reasoning,
            "node_history": ["classifier"],
        }

    except Exception as e:
        logger.warning(f"[{state['ticket_id']}] Classification failed: {e}")
        return {
            "intent": "unknown",
            "urgency": "high",
            "resolvability": "human",
            "confidence": 0.0,
            "classification_reasoning": f"Parse failure: {e}",
            "node_history": ["classifier"],
        }


# ============================================================
# Node 2: Context Fetcher
# ============================================================

async def context_fetcher_node(state: dict) -> dict:
    """
    Pre-fetch order, customer, and product data.
    Phase 1: customer + order in parallel.
    Phase 2: product fetched using product_id from order data.
    Failures are soft — downstream nodes handle None gracefully.
    """
    from ..tools.schemas import OrderData, CustomerData, ProductData

    order_data = None
    customer_data = None
    product_data = None
    new_tool_calls = []
    new_errors = []
    resolved_customer_id = ""

    # ── Phase 1: Fetch customer + order in parallel ──
    phase1_tasks = []
    phase1_names = []

    if state.get("customer_email"):
        phase1_tasks.append(
            retry_with_backoff(
                TOOL_REGISTRY["get_customer"],
                {"email": state["customer_email"]},
                CustomerData,
                tool_name="get_customer",
            )
        )
        phase1_names.append("get_customer")

    if state.get("order_id"):
        phase1_tasks.append(
            retry_with_backoff(
                TOOL_REGISTRY["get_order"],
                {"order_id": state["order_id"]},
                OrderData,
                tool_name="get_order",
            )
        )
        phase1_names.append("get_order")

    phase1_results = await asyncio.gather(*phase1_tasks) if phase1_tasks else []

    for result, name in zip(phase1_results, phase1_names):
        args = {}
        if name == "get_customer":
            args = {"email": state.get("customer_email", "")}
        elif name == "get_order":
            args = {"order_id": state.get("order_id", "")}

        new_tool_calls.append(_make_tool_call_record(result, args))

        if result.success:
            if name == "get_order":
                order_data = result.validated_data.model_dump() if hasattr(result.validated_data, "model_dump") else result.validated_data
            elif name == "get_customer":
                customer_data = result.validated_data.model_dump() if hasattr(result.validated_data, "model_dump") else result.validated_data
                resolved_customer_id = customer_data.get("customer_id", "")
        else:
            err_record = _make_error_record(result, "context_fetcher")
            err_record.recoverable = True
            new_errors.append(err_record)

    # ── Phase 1b: If no order_id was provided, try to find orders by customer ──
    if not state.get("order_id") and resolved_customer_id and "get_customer_orders" in TOOL_REGISTRY:
        from ..tools.schemas import OrderData as _OD
        try:
            orders_result = await retry_with_backoff(
                TOOL_REGISTRY["get_customer_orders"],
                {"customer_id": resolved_customer_id},
                _OD,
                tool_name="get_customer_orders",
            )
            new_tool_calls.append(_make_tool_call_record(orders_result, {"customer_id": resolved_customer_id}))
            if orders_result.success and orders_result.validated_data:
                # Store the list; resolver will pick the right one
                if isinstance(orders_result.validated_data, list):
                    order_data = [o.model_dump() if hasattr(o, "model_dump") else o for o in orders_result.validated_data]
                else:
                    order_data = orders_result.validated_data.model_dump() if hasattr(orders_result.validated_data, "model_dump") else orders_result.validated_data
        except Exception as e:
            logger.debug(f"[{state['ticket_id']}] get_customer_orders fallback failed: {e}")

    # ── Phase 2: Fetch product using product_id from order data ──
    product_id_to_fetch = None
    if isinstance(order_data, dict) and order_data.get("product_id"):
        product_id_to_fetch = order_data["product_id"]
    elif isinstance(order_data, list) and len(order_data) > 0:
        product_id_to_fetch = order_data[0].get("product_id")

    # Also check ticket text for P-XXX pattern
    if not product_id_to_fetch:
        pmatch = re.search(r'\bP\d{3}\b', state.get("ticket_text", ""))
        if pmatch:
            product_id_to_fetch = pmatch.group()

    if product_id_to_fetch:
        prod_result = await retry_with_backoff(
            TOOL_REGISTRY["get_product"],
            {"product_id": product_id_to_fetch},
            ProductData,
            tool_name="get_product",
        )
        new_tool_calls.append(_make_tool_call_record(prod_result, {"product_id": product_id_to_fetch}))
        if prod_result.success:
            product_data = prod_result.validated_data.model_dump() if hasattr(prod_result.validated_data, "model_dump") else prod_result.validated_data
        else:
            err = _make_error_record(prod_result, "context_fetcher")
            err.recoverable = True
            new_errors.append(err)

    context_incomplete = any([
        order_data is None and state.get("order_id") is not None,
        customer_data is None,
    ])

    logger.info(
        f"[{state['ticket_id']}] Context fetched: "
        f"order={'OK' if order_data else 'MISS'}, "
        f"customer={'OK' if customer_data else 'MISS'}, "
        f"product={'OK' if product_data else 'N/A'}, "
        f"customer_id={resolved_customer_id}"
    )

    return {
        "order_data": order_data,
        "customer_data": customer_data,
        "product_data": product_data,
        "customer_id": resolved_customer_id,
        "context_incomplete": context_incomplete,
        "tool_calls": new_tool_calls,
        "errors": new_errors,
        "node_history": ["context_fetcher"],
    }


# ============================================================
# Node 3: Router
# ============================================================

def router_node(state: dict) -> dict:
    """
    Pure routing logic — no LLM calls. Decides next action based on confidence and errors.
    """
    confidence = state.get("confidence", 0.0)
    resolvability = state.get("resolvability", "human")
    intent = state.get("intent", "unknown")
    errors = state.get("errors", [])
    urgency = state.get("urgency", "low")
    context_incomplete = state.get("context_incomplete", False)

    routing_decision = determine_routing(
        confidence=confidence,
        resolvability=resolvability,
        intent=intent,
        errors=errors,
        urgency=urgency,
        context_incomplete=context_incomplete
    )

    logger.info(
        f"[{state['ticket_id']}] Routing: {routing_decision} "
        f"(confidence={confidence:.2f}, resolvability={resolvability}, intent={intent})"
    )

    return {
        "routing_decision": routing_decision,
        "node_history": ["router"],
    }


# ============================================================
# Node 4: Resolver
# ============================================================

async def resolver_node(state: dict) -> dict:
    """
    Execute resolution strategy: escalation path, auto-resolve, or DLQ.
    """
    routing = state.get("routing_decision", "escalate")

    # --- DLQ Path (unrecoverable errors) ---
    if routing == "dlq":
        return {
            "resolution_status": "failed",
            "tool_calls": [],
            "errors": [],
            "node_history": ["resolver"],
        }

    # --- Escalation Path ---
    if routing == "escalate":
        return await _escalation_path(state)

    # --- Auto-Resolve Path ---
    return await _auto_resolve_path(state)


async def _escalation_path(state: dict) -> dict:
    """Handle escalation: call escalate() then send_reply() with acknowledgment."""
    from ..tools.schemas import EscalationResult, SendReplyResult

    urgency = state.get("urgency", "medium")
    priority = URGENCY_TO_PRIORITY_MAP.get(urgency, "medium")

    summary = json.dumps({
        "ticket_id": state["ticket_id"],
        "intent": state.get("intent"),
        "urgency": urgency,
        "confidence": state.get("confidence"),
        "customer_data": state.get("customer_data"),
        "order_data": state.get("order_data"),
        "reasoning": state.get("classification_reasoning"),
    }, default=str)

    # Tool call 1: escalate
    esc_args = {"ticket_id": state["ticket_id"], "summary": summary, "priority": priority}
    esc_result = await retry_with_backoff(
        TOOL_REGISTRY["escalate"], esc_args, EscalationResult, tool_name="escalate"
    )

    new_tool_calls = [_make_tool_call_record(esc_result, esc_args)]
    new_errors = []

    if not esc_result.success:
        new_errors.append(_make_error_record(esc_result, "resolver"))
        return {
            "resolution_status": "failed",
            "escalation_reason": "escalation_tool_failed",
            "tool_calls": new_tool_calls,
            "errors": new_errors,
            "node_history": ["resolver"],
        }

    # Tool call 2: send acknowledgment reply
    ack_msg = "Your request has been escalated to our specialist team. You'll hear back within 2-4 hours."
    ack_args = {"ticket_id": state["ticket_id"], "message": ack_msg}
    ack_result = await retry_with_backoff(
        TOOL_REGISTRY["send_reply"], ack_args, SendReplyResult, tool_name="send_reply"
    )
    new_tool_calls.append(_make_tool_call_record(ack_result, ack_args))

    if not ack_result.success:
        new_errors.append(_make_error_record(ack_result, "resolver"))

    logger.info(f"[{state['ticket_id']}] Escalated with priority={priority}")

    return {
        "resolution_status": "escalated",
        "escalation_reason": summary,
        "reply_text": ack_msg,
        "tool_calls": new_tool_calls,
        "errors": new_errors,
        "node_history": ["resolver"],
    }


async def _auto_resolve_path(state: dict) -> dict:
    """Auto-resolve using Groq's tool-use loop (max 5 iterations)."""
    from ..tools.schemas import SCHEMA_REGISTRY, SendReplyResult

    intent = state.get("intent", "unknown")
    tools = _get_tools_for_intent(intent)

    # Build context for the LLM
    context_parts = [
        f"Ticket ID: {state['ticket_id']}",
        f"Customer: {state.get('customer_id', 'Unknown')}",
        f"Intent: {intent}",
        f"Urgency: {state.get('urgency', 'medium')}",
    ]
    if state.get("order_data"):
        context_parts.append(f"Order Data: {json.dumps(state['order_data'], default=str)}")
    if state.get("customer_data"):
        context_parts.append(f"Customer Data: {json.dumps(state['customer_data'], default=str)}")
    if state.get("product_data"):
        context_parts.append(f"Product Data: {json.dumps(state['product_data'], default=str)}")

    category_instructions = {
        "refund_request": """
You are resolving a REFUND REQUEST. Follow this exact sequence:
1. Call check_refund_eligibility(order_id) to verify eligibility FIRST
2. If eligible AND refund amount <= $100: call issue_refund(order_id, amount), then send_reply with refund confirmation
3. If eligible AND refund amount > $100 AND your confidence is below 0.80: DO NOT issue refund, call escalate() instead
4. If NOT eligible: call search_knowledge_base("refund policy") then send_reply explaining the policy politely
NEVER call issue_refund without first confirming eligibility in this same chain.
""",
        "order_status": """
You are resolving an ORDER STATUS inquiry. Follow this exact sequence:
1. Call get_order(order_id) to fetch current status and tracking information
2. If order found: call send_reply with specific order status, estimated delivery date, and tracking number if available
3. If order not found or data incomplete: call escalate() with context
DO NOT send a generic reply. The customer wants their specific order status.
""",
        "product_inquiry": """
You are resolving a PRODUCT INQUIRY. Follow this exact sequence:
1. Call get_product(product_id) if a product ID or name is mentioned
2. Call search_knowledge_base(query) with the specific question about the product
3. Call send_reply with the specific product information, warranty details, and compatibility info found
""",
        "shipping_inquiry": """
You are resolving a SHIPPING INQUIRY. Follow this exact sequence:
1. Call get_order(order_id) to fetch shipping status and tracking
2. If in_transit or delayed: call search_knowledge_base("shipping policy delays") for relevant policy
3. Call send_reply with tracking status and estimated delivery information
""",
    }

    # Get category-specific instructions or fall back to generic
    category_instruction = category_instructions.get(intent, """
Resolve this customer inquiry by:
1. Calling the most relevant tool to get information about their issue
2. Calling search_knowledge_base if you need policy or FAQ information
3. Calling send_reply with a helpful, specific response
""")

    system_prompt = f"""You are a customer support agent for ShopWave, an e-commerce platform.
You must resolve the customer's issue by using the available tools.

Here is what we know so far:
{chr(10).join(context_parts)}

The customer wrote:
{state['ticket_text']}

{category_instruction}

IMPORTANT RULES:
1. Use tools to gather information and take action BEFORE writing a reply.
2. Always end by calling send_reply with a helpful, professional message to the customer.
3. For refund requests: ALWAYS check refund eligibility first, then issue the refund if eligible.
4. Be empathetic and professional in your reply.
5. After calling send_reply, STOP. Do not call any more tools."""

    messages = [{"role": "user", "content": system_prompt}]
    new_tool_calls = []
    new_errors = []
    reply_text = None
    refund_result = None
    sent_reply_via_tool = False

    client = _get_client()

    for iteration in range(5):
        try:
            response = await client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messages,
                tools=tools,
                max_tokens=1024,
            )
        except Exception as e:
            logger.error(f"[{state['ticket_id']}] Groq API error in resolver: {e}")
            new_errors.append(ErrorRecord(
                node="resolver", error_type="llm_error", message=str(e)[:500], recoverable=False
            ))
            break

        choice = response.choices[0]

        # Check if LLM is done (no more tool calls)
        if choice.finish_reason == "stop":
            # If the LLM produced text and we haven't sent a reply yet, use it
            if choice.message.content and not sent_reply_via_tool:
                reply_text = choice.message.content
            break

        # Process tool calls
        if choice.message.tool_calls:
            # Append the assistant message (with tool_calls) to conversation
            messages.append(choice.message)

            for tool_call in choice.message.tool_calls:
                tool_name = tool_call.function.name
                try:
                    tool_input = json.loads(tool_call.function.arguments) if isinstance(tool_call.function.arguments, str) else tool_call.function.arguments
                except json.JSONDecodeError:
                    tool_input = {}

                # --- Handle send_reply specially: this IS the final reply ---
                if tool_name == "send_reply":
                    reply_msg = tool_input.get("message", "")
                    reply_args = {"ticket_id": state["ticket_id"], "message": reply_msg}
                    send_result = await retry_with_backoff(
                        TOOL_REGISTRY["send_reply"], reply_args, SendReplyResult, tool_name="send_reply"
                    )
                    new_tool_calls.append(_make_tool_call_record(send_result, reply_args))

                    if send_result.success:
                        reply_text = reply_msg
                        sent_reply_via_tool = True
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": json.dumps({"status": "sent", "message_id": "MSG-OK"}),
                        })
                    else:
                        new_errors.append(_make_error_record(send_result, "resolver"))
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": json.dumps({"error": send_result.error_type}),
                        })
                    continue

                # Idempotency guard for issue_refund
                if tool_name == "issue_refund" and (state.get("refund_result") or refund_result):
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps({"error": "Refund already issued for this ticket"}),
                    })
                    continue

                # Secondary confidence check for high-value refunds
                if tool_name == "issue_refund":
                    amount = tool_input.get("amount", 0)
                    confidence = state.get("confidence", 0.0)
                    if amount > 100.0 and confidence < 0.80:
                        logger.info(f"[{state['ticket_id']}] High-value refund (${amount}) with low confidence ({confidence}) -- escalating")
                        esc_result = await _escalation_path(state)
                        # Preserve tool calls accumulated before the gate fired
                        esc_result["tool_calls"] = new_tool_calls + esc_result.get("tool_calls", [])
                        esc_result["errors"] = new_errors + esc_result.get("errors", [])
                        return esc_result

                # Execute the tool via retry wrapper
                schema = SCHEMA_REGISTRY.get(tool_name)
                if schema and tool_name in TOOL_REGISTRY:
                    result = await retry_with_backoff(
                        TOOL_REGISTRY[tool_name],
                        tool_input,
                        schema,
                        tool_name=tool_name,
                    )
                    new_tool_calls.append(_make_tool_call_record(result, tool_input))

                    if result.success:
                        if isinstance(result.validated_data, list):
                            result_data = [item.model_dump() if hasattr(item, "model_dump") else item for item in result.validated_data]
                        elif hasattr(result.validated_data, "model_dump"):
                            result_data = result.validated_data.model_dump()
                        else:
                            result_data = result.validated_data

                        # Track refund results for idempotency
                        if tool_name == "issue_refund":
                            refund_result = result_data

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": json.dumps(result_data, default=str),
                        })
                    else:
                        new_errors.append(_make_error_record(result, "resolver"))
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": json.dumps({"error": result.error_type, "message": result.error_message}),
                        })
                else:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps({"error": f"Unknown tool: {tool_name}"}),
                    })

            # If send_reply was called in this batch, we're done
            if sent_reply_via_tool:
                break

        else:
            # No tool calls and not "stop" — treat as final text reply
            if choice.message.content:
                reply_text = choice.message.content
            break

        # Small delay between iterations to reduce Groq rate limiting
        await asyncio.sleep(0.5)

    # --- Post-loop handling ---

    # If we already sent reply via tool, we're good
    if sent_reply_via_tool and reply_text:
        logger.info(f"[{state['ticket_id']}] Resolved with {len(new_tool_calls)} tool calls")
        result_dict = {
            "resolution_status": "resolved",
            "reply_text": reply_text,
            "tool_calls": new_tool_calls,
            "errors": new_errors,
            "node_history": ["resolver"],
        }
        if refund_result:
            result_dict["refund_result"] = refund_result
        return result_dict

    # If LLM produced text but didn't call send_reply, send it ourselves
    if reply_text and not sent_reply_via_tool:
        reply_args = {"ticket_id": state["ticket_id"], "message": reply_text}
        send_result = await retry_with_backoff(
            TOOL_REGISTRY["send_reply"], reply_args, SendReplyResult, tool_name="send_reply"
        )
        new_tool_calls.append(_make_tool_call_record(send_result, reply_args))
        if not send_result.success:
            new_errors.append(_make_error_record(send_result, "resolver"))

        logger.info(f"[{state['ticket_id']}] Resolved with {len(new_tool_calls)} tool calls")
        result_dict = {
            "resolution_status": "resolved" if send_result.success else "failed",
            "reply_text": reply_text,
            "tool_calls": new_tool_calls,
            "errors": new_errors,
            "node_history": ["resolver"],
        }
        if refund_result:
            result_dict["refund_result"] = refund_result
        return result_dict

    # Max iterations with no reply — send a fallback acknowledgment
    logger.warning(f"[{state['ticket_id']}] Resolver: no reply generated after max iterations, sending fallback")
    fallback_msg = "Thank you for contacting ShopWave support. We've received your request and a team member will follow up with you shortly."
    fallback_args = {"ticket_id": state["ticket_id"], "message": fallback_msg}
    fallback_result = await retry_with_backoff(
        TOOL_REGISTRY["send_reply"], fallback_args, SendReplyResult, tool_name="send_reply"
    )
    new_tool_calls.append(_make_tool_call_record(fallback_result, fallback_args))
    if not fallback_result.success:
        new_errors.append(_make_error_record(fallback_result, "resolver"))

    new_errors.append(ErrorRecord(
        node="resolver", error_type="max_iterations_exceeded",
        message="LLM did not produce a final reply within 5 iterations — fallback sent",
        recoverable=False,
    ))

    return {
        "resolution_status": "escalated",
        "reply_text": fallback_msg,
        "tool_calls": new_tool_calls,
        "errors": new_errors,
        "node_history": ["resolver"],
    }


# ============================================================
# Node 5: Audit Close
# ============================================================

async def audit_close_node(state: dict) -> dict:
    """
    Finalize the ticket: build audit record and determine if DLQ is needed.
    """
    now = datetime.now()
    started = state.get("started_at", now.isoformat())

    try:
        started_dt = datetime.fromisoformat(started) if isinstance(started, str) else started
    except (ValueError, TypeError):
        started_dt = now

    duration_ms = (now - started_dt).total_seconds() * 1000

    # Infer status: DLQ-routed tickets that skip resolver have None status
    resolution_status = state.get("resolution_status")
    if not resolution_status:
        if state.get("routing_decision") == "dlq":
            resolution_status = "failed"
        else:
            resolution_status = "unknown"

    try:
        audit_record = AuditRecord(
            ticket_id=state["ticket_id"],
            customer_id=state["customer_id"],
            customer_email=state.get("customer_email", ""),
            order_id=state.get("order_id"),
            intent=state.get("intent"),
            urgency=state.get("urgency"),
            resolvability=state.get("resolvability"),
            confidence=state.get("confidence"),
            classification_reasoning=state.get("classification_reasoning"),
            routing_decision=state.get("routing_decision"),
            resolution_status=resolution_status,
            reply_text=state.get("reply_text"),
            escalation_reason=state.get("escalation_reason"),
            node_history=state.get("node_history", []),
            tool_calls=state.get("tool_calls", []),
            errors=state.get("errors", []),
            total_duration_ms=duration_ms,
            started_at=started_dt,
            completed_at=now,
        )
        audit_dict = audit_record.model_dump(mode="json")
    except Exception as e:
        logger.error(f"[{state.get('ticket_id', 'unknown')}] Failed to create AuditRecord: {e}")
        # Fallback dict that won't crash the system
        audit_dict = {
            "ticket_id": state.get("ticket_id", "unknown"),
            "resolution_status": resolution_status,
            "error_in_audit": str(e)
        }

    logger.info(
        f"[{state['ticket_id']}] Audit close: status={resolution_status}, "
        f"tools={len(state.get('tool_calls', []))}, "
        f"duration={duration_ms:.0f}ms"
    )

    return {
        "audit_record": audit_dict,
        "resolution_status": resolution_status,
        "node_history": ["audit_close"],
    }
