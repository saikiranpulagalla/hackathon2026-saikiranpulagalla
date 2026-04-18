"""ShopWave Support Agent — Unified Dashboard & Live Testing."""

import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
import json
import os
from datetime import datetime
from pathlib import Path

# --- Page Config ---
st.set_page_config(
    page_title="ShopWave Support Agent",
    page_icon="🛒",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# --- Custom CSS ---
st.markdown("""
<style>
    .block-container { padding-top: 1rem; }
    h1 { color: #1a1a2e; }
    .stMetric > div { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 1rem; border-radius: 10px; color: white; }
    .stMetric label { color: rgba(255,255,255,0.8) !important; }
    .stMetric [data-testid="stMetricValue"] { color: white !important; }
    .resolved-badge { background-color: #28a745; color: white; padding: 4px 12px; border-radius: 12px; font-weight: bold; }
    .escalated-badge { background-color: #fd7e14; color: white; padding: 4px 12px; border-radius: 12px; font-weight: bold; }
    .stTab [data-baseweb="tab-list"] { gap: 8px; }
</style>
""", unsafe_allow_html=True)

# --- Header ---
st.title("🛒 ShopWave Support Agent")
st.caption("KSOLVES Agentic AI Hackathon 2026 — AI First, Always")

# --- Tabs ---
tab_live, tab_analytics = st.tabs(["🧪 Live Agent Testing", "📊 Analytics Dashboard"])

# ============================================================
# TAB 1: LIVE AGENT TESTING
# ============================================================
with tab_live:
    from live_tab import render_live_tab
    render_live_tab()

# ============================================================
# TAB 2: ANALYTICS DASHBOARD (all existing code preserved)
# ============================================================
with tab_analytics:
    project_root = Path(__file__).parent
    audit_path = project_root / "audit_log.json"
    dlq_path = project_root / "dlq.json"

    if not audit_path.exists():
        st.warning("📂 No audit log found yet.")
        st.info("**To populate this dashboard:** Run `python -m src.main` first to process all 20 tickets, then refresh this page.")
        st.code("python -m src.main", language="bash")
    else:
        with open(audit_path) as f:
            audit_data = json.load(f)

        dlq_data = []
        if dlq_path.exists():
            with open(dlq_path) as f:
                dlq_data = json.load(f)

        # SECTION 1: Run Summary
        st.markdown("---")
        st.subheader("📊 Run Summary")

        total = len(audit_data)
        resolved = sum(1 for r in audit_data if r.get("resolution_status") == "resolved")
        escalated = sum(1 for r in audit_data if r.get("resolution_status") == "escalated")
        failed = sum(1 for r in audit_data if r.get("resolution_status") not in ("resolved", "escalated"))
        dlq_count = len(dlq_data)

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Tickets", total)
        col2.metric("Resolved", resolved, delta=f"{resolved / max(total, 1) * 100:.0f}%")
        col3.metric("Escalated", escalated, delta=f"{escalated / max(total, 1) * 100:.0f}%")
        col4.metric("DLQ / Failed", dlq_count + failed)

        # SECTION 2: Resolution Breakdown
        st.markdown("---")
        st.subheader("📈 Resolution Breakdown")

        left_col, right_col = st.columns(2)

        with left_col:
            status_counts = {"Resolved": resolved, "Escalated": escalated, "Failed": failed}
            if dlq_count > 0:
                status_counts["DLQ"] = dlq_count
            fig_pie = px.pie(
                names=list(status_counts.keys()),
                values=list(status_counts.values()),
                color_discrete_sequence=["#2ecc71", "#f39c12", "#e74c3c", "#8e44ad"],
                title="Outcome Distribution",
                hole=0.4,
            )
            fig_pie.update_traces(textinfo="label+percent+value")
            st.plotly_chart(fig_pie, width="stretch")

        with right_col:
            intents = [r.get("intent", "unknown") for r in audit_data]
            intent_counts = pd.Series(intents).value_counts().reset_index()
            intent_counts.columns = ["Intent", "Count"]
            fig_bar = px.bar(
                intent_counts, x="Intent", y="Count",
                color="Intent",
                title="Tickets by Intent Category",
                color_discrete_sequence=px.colors.qualitative.Set2,
            )
            fig_bar.update_layout(showlegend=False)
            st.plotly_chart(fig_bar, width="stretch")

        # SECTION 3: Confidence Distribution
        st.markdown("---")
        st.subheader("🎯 Confidence Distribution")

        confidences = [r.get("confidence", 0) for r in audit_data if r.get("confidence") is not None]
        if confidences:
            fig_hist = px.histogram(
                x=confidences, nbins=20,
                labels={"x": "Confidence Score", "y": "Ticket Count"},
                title="Classification Confidence Across All Tickets",
                color_discrete_sequence=["#667eea"],
            )
            fig_hist.add_vline(x=0.65, line_dash="dash", line_color="red",
                                annotation_text="Primary Gate (0.65)", annotation_position="top left")
            fig_hist.add_vline(x=0.80, line_dash="dash", line_color="orange",
                                annotation_text="High-Value Refund Gate (0.80)", annotation_position="top right")
            fig_hist.update_layout(bargap=0.1)
            st.plotly_chart(fig_hist, width="stretch")
            st.caption("Tickets below 0.65 are escalated regardless of category. "
                       "Refunds >$100 with confidence 0.65-0.79 are also escalated.")

        # SECTION 4: Tool Performance Table
        st.markdown("---")
        st.subheader("🔧 Tool Performance")

        tool_stats = {}
        for record in audit_data:
            for tc in record.get("tool_calls", []):
                name = tc.get("tool_name", "unknown")
                if name not in tool_stats:
                    tool_stats[name] = {"total": 0, "success": 0, "latencies": [], "timeouts": 0, "retries": 0}
                stats = tool_stats[name]
                stats["total"] += 1
                if tc.get("success"):
                    stats["success"] += 1
                if tc.get("error_type") == "timeout":
                    stats["timeouts"] += 1
                attempt = tc.get("attempt", 1)
                if attempt > 1:
                    stats["retries"] += 1
                dur = tc.get("duration_ms", 0)
                if dur:
                    stats["latencies"].append(dur)

        if tool_stats:
            rows = []
            for name in sorted(tool_stats.keys()):
                s = tool_stats[name]
                rate = s["success"] / max(s["total"], 1) * 100
                avg_lat = sum(s["latencies"]) / max(len(s["latencies"]), 1)
                rows.append({
                    "Tool Name": name,
                    "Total Calls": s["total"],
                    "Success Rate": f"{rate:.0f}%",
                    "Avg Latency (ms)": f"{avg_lat:.0f}",
                    "Timeout Count": s["timeouts"],
                    "Retry Count": s["retries"],
                })
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

        # SECTION 5: Audit Log Explorer
        st.markdown("---")
        st.subheader("🔍 Audit Log Explorer")

        summary_rows = []
        for r in audit_data:
            summary_rows.append({
                "Ticket ID": r.get("ticket_id", "?"),
                "Intent": r.get("intent", "?"),
                "Urgency": r.get("urgency", "?"),
                "Confidence": r.get("confidence", 0),
                "Outcome": r.get("resolution_status", "?"),
                "Tool Calls": len(r.get("tool_calls", [])),
                "Duration (ms)": f"{r.get('total_duration_ms', 0):.0f}",
            })

        df_summary = pd.DataFrame(summary_rows)
        st.dataframe(df_summary, width="stretch", hide_index=True)

        st.markdown("**Click a ticket below to inspect its full reasoning chain:**")
        for r in audit_data:
            tid = r.get("ticket_id", "?")
            status = r.get("resolution_status", "?")
            emoji = "✅" if status == "resolved" else "⬆️" if status == "escalated" else "❌"
            with st.expander(f"{emoji} {tid} — {r.get('intent', '?')} — {status}"):
                col_a, col_b = st.columns(2)
                with col_a:
                    st.markdown(f"**Intent:** {r.get('intent')}")
                    st.markdown(f"**Urgency:** {r.get('urgency')}")
                    st.markdown(f"**Confidence:** {r.get('confidence')}")
                    st.markdown(f"**Routing:** {r.get('routing_decision')}")
                with col_b:
                    st.markdown(f"**Status:** {status}")
                    st.markdown(f"**Duration:** {r.get('total_duration_ms', 0):.0f}ms")
                    st.markdown(f"**Nodes:** {' → '.join(r.get('node_history', []))}")

                st.markdown("**Classification Reasoning:**")
                st.info(r.get("classification_reasoning", "N/A"))

                tool_calls = r.get("tool_calls", [])
                if tool_calls:
                    st.markdown("**Tool Call Timeline:**")
                    for i, tc in enumerate(tool_calls, 1):
                        status_icon = "✅" if tc.get("success") else "❌"
                        attempt_str = f" (attempt {tc.get('attempt', 1)})" if tc.get("attempt", 1) > 1 else ""
                        st.text(f"  {i}. {status_icon} {tc.get('tool_name', '?')}{attempt_str} — {tc.get('duration_ms', 0):.0f}ms")

                if r.get("reply_text"):
                    st.markdown("**Reply sent to customer:**")
                    st.success(r["reply_text"][:500])
                if r.get("escalation_reason"):
                    st.markdown("**Escalation reason:**")
                    st.warning(str(r["escalation_reason"])[:500])

                errors = r.get("errors", [])
                if errors:
                    st.markdown("**Errors encountered:**")
                    for err in errors:
                        if isinstance(err, dict):
                            st.error(f"{err.get('error_type', '?')}: {err.get('message', '')}")
                        else:
                            st.error(str(err))

        # SECTION 6: DLQ Viewer
        st.markdown("---")
        st.subheader("☠️ Dead Letter Queue")

        if dlq_data:
            st.warning(f"⚠️ {len(dlq_data)} tickets in Dead Letter Queue")
            for entry in dlq_data:
                with st.expander(f"❌ {entry.get('ticket_id', '?')} — {entry.get('error_type', '?')}"):
                    st.json(entry)
        else:
            st.success("✅ Dead Letter Queue is empty — all tickets resolved or escalated")

        # SECTION 7: Concurrent Processing Proof
        st.markdown("---")
        st.subheader("⚡ Concurrent Processing Proof")

        gantt_data = []
        for r in audit_data:
            started = r.get("started_at")
            completed = r.get("completed_at")
            if started and completed:
                try:
                    s = datetime.fromisoformat(str(started).replace("Z", "+00:00")) if isinstance(started, str) else started
                    e = datetime.fromisoformat(str(completed).replace("Z", "+00:00")) if isinstance(completed, str) else completed
                    gantt_data.append({
                        "Ticket": r.get("ticket_id", "?"),
                        "Start": s,
                        "End": e,
                        "Status": r.get("resolution_status", "?"),
                    })
                except (ValueError, TypeError):
                    pass

        if gantt_data:
            df_gantt = pd.DataFrame(gantt_data)
            color_map = {"resolved": "#2ecc71", "escalated": "#f39c12", "failed": "#e74c3c", "unknown": "#95a5a6"}

            fig_gantt = px.timeline(
                df_gantt, x_start="Start", x_end="End", y="Ticket",
                color="Status",
                color_discrete_map=color_map,
                title="Ticket Execution Timeline (Gantt Chart)",
            )
            fig_gantt.update_yaxes(autorange="reversed")
            fig_gantt.update_layout(height=max(400, len(gantt_data) * 30))
            st.plotly_chart(fig_gantt, width="stretch")
            st.caption("Overlapping bars demonstrate asyncio.Semaphore concurrent processing")

            all_starts = [d["Start"] for d in gantt_data]
            all_ends = [d["End"] for d in gantt_data]
            wall_clock = (max(all_ends) - min(all_starts)).total_seconds()
            individual_times = [(d["End"] - d["Start"]).total_seconds() for d in gantt_data]
            sequential_est = sum(individual_times)
            speedup = sequential_est / max(wall_clock, 0.1)

            col_t1, col_t2, col_t3 = st.columns(3)
            col_t1.metric("Wall-clock time", f"{wall_clock:.1f}s")
            col_t2.metric("Est. sequential time", f"{sequential_est:.1f}s")
            col_t3.metric("Concurrency speedup", f"{speedup:.1f}x")
        else:
            st.info("No timing data available for Gantt chart.")

        # Footer
        st.markdown("---")
        st.caption("Built for the KSOLVES Agentic AI Hackathon 2026 • ShopWave Support Agent")
