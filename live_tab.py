"""Live Agent Testing tab for the Streamlit dashboard."""
import streamlit as st
import json, os, sys, asyncio, threading, time, uuid
from pathlib import Path
from datetime import datetime

import nest_asyncio
nest_asyncio.apply()


def _tc_get(tc, key, default=None):
    """Safely get a field from a tool call (dict or object)."""
    if isinstance(tc, dict):
        return tc.get(key, default)
    return getattr(tc, key, default)

# --- Example Tickets ---
EXAMPLES = [
    ("✅ Refund (Eligible)",
     "Hi, I need to return my order ORD-7823 placed last week. The laptop keyboard stopped working after 2 days. I'd like a full refund please.",
     "alice@example.com",
     "Expected: AUTO-RESOLVE → 4 tool calls → issue_refund → send_reply"),
    ("💰 Refund (High Value >$100)",
     "I want to return the premium camera bundle I ordered (ORD-9934), it was $349. The image quality is much worse than advertised.",
     "bob@example.com",
     "Expected: ESCALATE via secondary gate. Set DEMO_CONFIDENCE_OVERRIDE=0.72 in .env to demonstrate this gate. With override: confidence 0.72 < 0.80 threshold for high-value refunds → escalates."),
    ("📦 Order Status",
     "Where is my package? I ordered 5 days ago (ORD-2291) and still haven't received a tracking number. Starting to get worried.",
     "carol@example.com",
     "Expected: AUTO-RESOLVE → get_order → send_reply with tracking info"),
    ("🔧 Technical Issue",
     "I downloaded the app but it crashes every time I try to log in. I've tried reinstalling 3 times. This is urgent as I need it for work.",
     "dave@example.com",
     "Expected: ESCALATE → technical issues routed to human specialists"),
    ("❓ Ambiguous (Low Conf)",
     "something is wrong with my account. I need help.",
     "eve@example.com",
     "Expected: ESCALATE → classifier confidence < 0.65 triggers primary gate"),
    ("👑 VIP Complaint",
     "This is completely unacceptable! My order ORD-5512 arrived damaged for the THIRD time. I'm a premium member and I expect better service. I want a full refund AND compensation.",
     "frank@example.com",
     "Expected: ESCALATE → VIP tier + high urgency → conservative routing"),
]


async def _run_agent_async(ticket_state: dict) -> dict:
    """Run the LangGraph workflow for a single ticket."""
    from dotenv import load_dotenv
    load_dotenv()
    from src.agent.graph import build_workflow
    workflow = build_workflow()
    return await workflow.ainvoke(ticket_state)


import concurrent.futures

def run_agent_sync(ticket_state: dict) -> dict:
    """Run async agent in a completely isolated process-level event loop"""
    import asyncio
    import sys
    
    # Force a completely fresh event loop
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        return loop.run_until_complete(_run_agent_async(ticket_state))
    except Exception as e:
        return {"resolution_status": "failed", "ticket_id": ticket_state.get("ticket_id", "?"),
                "errors": [{"error_type": type(e).__name__, "message": str(e), "recoverable": False}],
                "node_history": [], "tool_calls": []}
    finally:
        try:
            # Cancel pending tasks
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        finally:
            loop.close()


def render_live_tab():
    """Render the Live Agent Testing tab content."""

    # --- Load env vars ---
    from dotenv import load_dotenv
    load_dotenv()

    # --- API Key Check ---
    if not os.getenv("GROQ_API_KEY"):
        st.error("⚠️ GROQ_API_KEY not found. Add it to your `.env` file.")
        st.code("GROQ_API_KEY=your_key_here", language="bash")
        st.info("Get a free key at https://console.groq.com/keys")
        return

    demo_override = float(os.getenv("DEMO_CONFIDENCE_OVERRIDE", "0"))
    if demo_override > 0:
        st.warning(f"⚠️ DEMO MODE: Confidence override active at {demo_override:.0%}. Set DEMO_CONFIDENCE_OVERRIDE=0 in .env to disable.")

    # --- Example Tickets ---
    st.markdown("#### 📋 Quick Test Tickets")
    st.caption("This agent handles **e-commerce support tickets** — refunds, order status, product issues, and complaints. "
               "Click any example to auto-fill, or type your own customer support scenario.")

    cols = st.columns(3)
    for i, (label, text, email, hint) in enumerate(EXAMPLES):
        with cols[i % 3]:
            if st.button(label, key=f"ex_{i}", use_container_width=True):
                st.session_state._pending_text = text
                st.session_state._pending_email = email
                st.session_state.example_label = hint
                st.rerun()

    # Apply pending example values before widgets render
    if "_pending_text" in st.session_state:
        st.session_state.ticket_text = st.session_state.pop("_pending_text")
    if "_pending_email" in st.session_state:
        st.session_state.customer_email = st.session_state.pop("_pending_email")

    if st.session_state.get("example_label"):
        st.info(f"💡 {st.session_state.example_label}")

    # --- Input Form ---
    st.markdown("---")
    st.markdown("#### ✏️ Submit a Ticket")
    left, right = st.columns([2, 1])
    with left:
        ticket_text = st.text_area("Ticket Content", key="ticket_text", height=120,
                                   placeholder="Type a customer support ticket, or click an example above...")
    with right:
        email = st.text_input("Customer Email", key="customer_email", placeholder="customer@example.com")

    process = st.button("🚀 Process Ticket", type="primary", use_container_width=True, key="process_btn")

    # --- Process ---
    if process:
        if not st.session_state.get("ticket_text", "").strip():
            st.warning("Please enter a ticket or click one of the example tickets above.")
            return

        ticket_id = f"TKT-LIVE-{str(uuid.uuid4())[:8].upper()}"
        cust_email = st.session_state.get("customer_email", "test@example.com") or "test@example.com"

        # Extract order_id from ticket text (if present)
        import re
        order_match = re.search(r'ORD-\d+', st.session_state.ticket_text)
        extracted_order_id = order_match.group() if order_match else None

        initial_state = {
            "ticket_id": ticket_id, "ticket_text": st.session_state.ticket_text,
            "customer_id": f"CUST-{str(uuid.uuid4())[:6].upper()}",
            "customer_email": cust_email, "order_id": extracted_order_id,
            "intent": None, "urgency": None, "resolvability": None,
            "confidence": None, "classification_reasoning": None,
            "order_data": None, "customer_data": None, "product_data": None,
            "knowledge_results": None, "context_incomplete": False,
            "tool_calls": [], "errors": [],
            "node_history": [], "retry_counts": {}, "routing_decision": None,
            "resolution_status": None, "reply_text": None,
            "escalation_reason": None, "refund_result": None,
            "audit_record": None, "started_at": datetime.now().isoformat(),
        }

        with st.status("🔄 Processing ticket through agent pipeline...", expanded=True) as status:
            st.write("📋 Initializing ticket state...")

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(run_agent_sync, initial_state)
                
                steps = [("🤖 Classifier Node (Groq LLM)...", 0.5),
                         ("🔍 Context Fetcher (parallel tool calls)...", 0.8),
                         ("🧭 Router Node (confidence evaluation)...", 0.5),
                         ("⚙️ Resolver Node (tool chain execution)...", 1.2),
                         ("📝 Audit Close Node...", 0.3)]
                
                for txt, delay in steps:
                    if future.done():
                        break
                    st.write(txt)
                    time.sleep(delay)

                try:
                    result = future.result(timeout=120)
                    status.update(label="✅ Processing complete!", state="complete", expanded=False)
                    st.session_state.last_result = result
                    st.session_state.last_ticket_id = ticket_id
                except concurrent.futures.TimeoutError:
                    status.update(label="❌ Timed out", state="error")
                    st.error("Agent timed out after 2 minutes.")
                    return

    # --- Display Results ---
    result = st.session_state.get("last_result")
    if not result:
        return

    st.markdown("---")
    st.markdown("### 📊 Results")

    outcome = result.get("resolution_status", "unknown")
    confidence = result.get("confidence") or 0
    routing = result.get("routing_decision", "unknown")
    intent = result.get("intent", "unknown")
    urgency = result.get("urgency", "unknown")

    # 1. Outcome header
    c1, c2, c3 = st.columns(3)
    with c1:
        if outcome == "resolved": st.success("✅ RESOLVED")
        elif outcome == "escalated": st.warning("⬆️ ESCALATED")
        else: st.error("❌ FAILED / DLQ")
    with c2:
        st.metric("Confidence", f"{confidence:.0%}")
        conf_label = "🟢 Above all gates" if confidence >= 0.80 else "🟡 Between gates" if confidence >= 0.65 else "🔴 Below primary"
        st.caption(conf_label)
    with c3:
        st.metric("Routing", routing.replace("_", " ").title() if routing else "Unknown")
        st.caption(f"Intent: `{intent or 'unknown'}` | Urgency: `{urgency or 'unknown'}`")

    # 2. Confidence explanation
    with st.expander("📊 Why was this routing decision made?", expanded=True):
        import plotly.graph_objects as go
        fig = go.Figure(go.Bar(x=[confidence], y=["Confidence"], orientation="h",
                               marker_color="#667eea", text=[f"{confidence:.0%}"], textposition="outside"))
        fig.add_vline(x=0.65, line_dash="dash", line_color="red", annotation_text="Primary (0.65)")
        fig.add_vline(x=0.80, line_dash="dash", line_color="orange", annotation_text="High-Value (0.80)")
        fig.update_layout(xaxis_range=[0, 1], height=120, margin=dict(t=30, b=10, l=10, r=10))
        st.plotly_chart(fig, use_container_width=True)

        if confidence < 0.65:
            st.info(f"🔴 Confidence ({confidence:.0%}) fell below 0.65. Agent escalated to human review.")
        elif confidence < 0.80 and intent == "refund_request":
            st.info(f"🟡 Confidence ({confidence:.0%}) is 0.65–0.79. For refunds, secondary gate applies.")
        else:
            st.info(f"🟢 Confidence ({confidence:.0%}) is above all thresholds.")

        reasoning = result.get("classification_reasoning", "")
        if reasoning:
            st.markdown(f"**Classifier reasoning:** {reasoning}")

    # 3. Tool call chain
    tool_calls = result.get("tool_calls", [])
    if tool_calls:
        st.markdown(f"### 🔧 Tool Call Chain ({len(tool_calls)} calls)")
        for i, tc in enumerate(tool_calls):
            c1, c2, c3, c4 = st.columns([0.5, 2, 1, 1])
            with c1: st.markdown(f"**{i+1}**")
            with c2:
                st.markdown(f"`{_tc_get(tc, 'tool_name', '?')}`")
                if _tc_get(tc, 'attempt', 1) > 1:
                    st.caption(f"⚠️ Attempt {_tc_get(tc, 'attempt')} (retried)")
            with c3:
                if _tc_get(tc, 'success'):
                    st.markdown("**✅ Success**")
                else:
                    st.markdown(f"**❌ {_tc_get(tc, 'error_type', 'error')}**")
            with c4:
                st.caption(f"{_tc_get(tc, 'duration_ms', 0):.0f}ms")
        ctx = [c for c in tool_calls if _tc_get(c, 'tool_name') in ('get_customer', 'get_order', 'get_product')]
        if len(ctx) >= 2:
            st.info("⚡ **Parallel execution:** context tools ran simultaneously via `asyncio.gather()`")

    # 4. Reply / Escalation
    reply = result.get("reply_text", "")
    esc = result.get("escalation_reason", "")
    if reply:
        st.markdown("### 💬 Reply Sent to Customer")
        st.markdown(f'<div style="background:#e8f4f8;border-left:4px solid #17a2b8;padding:12px;border-radius:4px">{reply}</div>', unsafe_allow_html=True)

    # Show design-intent note for low-confidence escalations
    if outcome == "escalated" and confidence < 0.65:
        st.markdown("### 🛡️ Safety Gate Activated")
        st.info(
            f"**Why this was escalated:** The classifier confidence ({confidence:.0%}) fell below the "
            f"0.65 safety threshold. This is **intentional** — the agent follows the principle "
            f"*\"when uncertain, do nothing and ask.\"* Rather than guessing and potentially taking "
            f"a wrong irreversible action (like issuing an incorrect refund), the agent routes to a "
            f"human specialist who can ask clarifying questions.\n\n"
            f"**In production**, the human agent would respond with: "
            f"*\"Hi! Thanks for reaching out. Could you tell me more about what you need help with? "
            f"For example, are you looking for help with an order, a refund, or a product issue?\"*"
        )
    if esc:
        st.markdown("### ⬆️ Escalation Summary")
        with st.expander("View escalation payload"):
            try:
                st.json(json.loads(esc) if isinstance(esc, str) else esc)
            except Exception:
                st.text(str(esc))

    # 5. Node path
    nodes = result.get("node_history", [])
    if nodes:
        st.markdown("### 🗺️ Execution Path")
        icons = {"classifier": "🤖", "context_fetcher": "🔍", "router": "🧭",
                 "resolver": "⚙️", "escalation": "⬆️", "audit_close": "📝"}
        ncols = st.columns(len(nodes))
        for col, n in zip(ncols, nodes):
            with col:
                st.markdown(f"**{icons.get(n, '•')}**")
                st.caption(n.replace("_", " ").title())

    # 6. Errors
    errors = result.get("errors", [])
    if errors:
        with st.expander(f"⚠️ {len(errors)} error(s) (all handled gracefully)"):
            for err in errors:
                etype = _tc_get(err, "error_type", "?")
                msg = _tc_get(err, "message", "")
                recov = _tc_get(err, "recoverable", False)
                r = "🔄 Recovered" if recov else "❌ Unrecoverable"
                st.markdown(f"**{r}** — `{etype}`: {msg}")

    # 7. Full audit JSON
    def _to_dict(obj):
        if isinstance(obj, dict):
            return obj
        if hasattr(obj, "model_dump"):
            return obj.model_dump(mode="json")
        if hasattr(obj, "__dict__"):
            return obj.__dict__
        return str(obj)

    with st.expander("📄 Full Audit Log Entry (JSON)"):
        audit = {"ticket_id": result.get("ticket_id", st.session_state.get("last_ticket_id")),
                 "intent": intent, "confidence": confidence, "routing_decision": routing,
                 "resolution_status": outcome,
                 "tool_calls": [_to_dict(tc) for tc in tool_calls],
                 "errors": [_to_dict(e) for e in errors],
                 "node_history": nodes, "reply_text": reply, "escalation_reason": esc,
                 "generated_at": datetime.utcnow().isoformat() + "Z"}
        st.json(audit)
        st.download_button("⬇️ Download audit entry", json.dumps(audit, indent=2, default=str),
                           f"audit_{audit['ticket_id']}.json", "application/json")

