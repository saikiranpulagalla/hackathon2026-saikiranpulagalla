"""Quick audit log analysis script."""
import json
import sys
sys.stdout.reconfigure(encoding='utf-8')

data = json.load(open("audit_log.json", encoding="utf-8"))
statuses = [r.get("resolution_status") for r in data]
print(f"Total: {len(data)}")
print(f"Resolved: {statuses.count('resolved')}")
print(f"Escalated: {statuses.count('escalated')}")
print(f"Failed: {statuses.count('failed')}")
print()

for r in data:
    tid = r.get("ticket_id", "?")
    intent = r.get("intent", "?")
    conf = r.get("confidence", 0)
    routing = r.get("routing_decision", "?")
    status = r.get("resolution_status", "?")
    resolvability = r.get("resolvability", "?")
    urgency = r.get("urgency", "?")
    n_tools = len(r.get("tool_calls", []))
    n_errors = len(r.get("errors", []))
    
    flags = []
    if routing == "auto_resolve" and status == "escalated":
        flags.append("ROUTED-AUTO-BUT-ESCALATED")
    if routing == "auto_resolve" and status != "resolved":
        flags.append(f"AUTO-RESOLVE-BUT-{status}")
    if conf >= 0.65 and routing == "escalate":
        always_esc = {"technical_support","billing_dispute","legal_threat","account_security","complaint"}
        if intent not in always_esc and resolvability != "human":
            flags.append(f"HIGH-CONF-ESCALATED")
    if n_errors > 0:
        flags.append(f"ERRORS:{n_errors}")
    
    flag_str = " << " + ", ".join(flags) if flags else ""
    print(f"{tid:12s} | {intent:20s} | conf={conf:.2f} | resolv={resolvability:6s} | urg={urgency:7s} | route={routing:14s} | {status:10s} | tools={n_tools}{flag_str}")

# Summarize why escalated tickets were escalated
print("\n--- WHY TICKETS WERE ESCALATED ---")
for r in data:
    if r.get("resolution_status") == "escalated":
        tid = r.get("ticket_id")
        intent = r.get("intent", "?")
        conf = r.get("confidence", 0)
        resolvability = r.get("resolvability", "?")
        routing = r.get("routing_decision", "?")
        urgency = r.get("urgency", "?")
        errors = r.get("errors", [])
        
        reasons = []
        always_esc = {"technical_support","billing_dispute","legal_threat","account_security","complaint"}
        if intent in always_esc:
            reasons.append(f"ALWAYS_ESCALATE_INTENT({intent})")
        if resolvability == "human":
            reasons.append("resolvability=human")
        if conf < 0.65:
            reasons.append(f"conf {conf:.2f} < 0.65")
        if routing == "auto_resolve":
            # It was auto-resolved but ended up escalated = secondary gate or resolver fallback
            err_types = [e.get("error_type","") if isinstance(e, dict) else "" for e in errors]
            if "llm_error" in err_types or "max_iterations_exceeded" in err_types:
                reasons.append("LLM_ERROR/MAX_ITER -> fallback escalation")
            else:
                reasons.append("secondary gate or resolver-level escalation")
        if not reasons:
            reasons.append("UNKNOWN - needs investigation")
        
        print(f"  {tid}: {', '.join(reasons)}")

print("\n--- ERROR DETAILS ---")
for r in data:
    errors = r.get("errors", [])
    if errors:
        print(f"\n{r['ticket_id']} (status={r.get('resolution_status')}):")
        for e in errors:
            if isinstance(e, dict):
                etype = e.get('error_type','?')
                msg = str(e.get('message',''))[:120]
                print(f"  [{etype}] {msg}")
