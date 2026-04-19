"""ShopWave Support Agent — main entry point."""

from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

# --- LangSmith Tracing Setup (must be before other imports) ---
import os
os.environ["LANGCHAIN_TRACING_V2"] = os.getenv("LANGCHAIN_TRACING_V2", "false")
if os.getenv("LANGSMITH_API_KEY"):
    os.environ["LANGCHAIN_API_KEY"] = os.getenv("LANGSMITH_API_KEY")
os.environ["LANGCHAIN_PROJECT"] = os.getenv("LANGSMITH_PROJECT", "shopwave-hackathon-2026")

import asyncio
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

# Environment variables loaded at the top
from src.agent.graph import build_workflow
from src.agent.state import AuditRecord, DLQEntry, RawTicket
from src.evaluation.metrics import ProcessingReport
from src.infrastructure.audit import AuditLogger
from src.infrastructure.dlq import DeadLetterQueue

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


async def process_all_tickets(
    tickets: list[RawTicket],
    audit_path: str = "audit_log.json",
    dlq_path: str = "dlq.json",
    max_concurrent: int = 3,
) -> ProcessingReport:
    """
    Process all tickets concurrently with a semaphore cap.

    Returns a ProcessingReport with summary metrics.
    """
    semaphore = asyncio.Semaphore(max_concurrent)
    workflow = build_workflow()
    audit_logger = AuditLogger(audit_path)
    dlq = DeadLetterQueue()
    report = ProcessingReport()
    start_time = time.monotonic()

    async def run_one(ticket: RawTicket) -> dict | Exception:
        """Process a single ticket within the semaphore."""
        async with semaphore:
            try:
                initial_state = {
                    "ticket_id": ticket.ticket_id,
                    "ticket_text": ticket.ticket_text,
                    "customer_id": ticket.customer_id,
                    "customer_email": ticket.customer_email,
                    "order_id": ticket.order_id,
                    "intent": None,
                    "urgency": None,
                    "resolvability": None,
                    "confidence": None,
                    "classification_reasoning": None,
                    "order_data": None,
                    "customer_data": None,
                    "product_data": None,
                    "knowledge_results": None,
                    "context_incomplete": False,
                    "tool_calls": [],
                    "errors": [],
                    "node_history": [],
                    "retry_counts": {},
                    "routing_decision": None,
                    "resolution_status": None,
                    "reply_text": None,
                    "escalation_reason": None,
                    "refund_result": None,
                    "audit_record": None,
                    "started_at": datetime.now().isoformat(),
                }
                final_state = await workflow.ainvoke(initial_state)
                return final_state
            except Exception as e:
                logger.error(f"[{ticket.ticket_id}] Unhandled exception: {e}")
                return e

    # Create tasks for all tickets
    tasks = [run_one(ticket) for ticket in tickets]

    # Run all concurrently
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Process results
    for ticket, result in zip(tickets, results):
        if isinstance(result, Exception):
            logger.error(f"[{ticket.ticket_id}] Failed with exception: {result}")
            report.add_exception(ticket.ticket_id, result)
            entry = DLQEntry.from_exception(ticket.ticket_id, result)
            await dlq.push(entry)
        elif isinstance(result, dict):
            report.add_result(result)

            # Write audit record
            audit_data = result.get("audit_record")
            if audit_data:
                try:
                    record = AuditRecord(**audit_data)
                    await audit_logger.write(record)
                except Exception as e:
                    logger.error(f"[{ticket.ticket_id}] Failed to write audit record: {e}")

            # Push to DLQ if failed
            if result.get("resolution_status") == "failed":
                entry = DLQEntry.from_state(result)
                await dlq.push(entry)

    # Save outputs
    report.elapsed_seconds = time.monotonic() - start_time
    report.dlq_count = dlq.size

    await audit_logger.save()
    await dlq.dump(dlq_path)

    logger.info(f"Audit log saved to {audit_path} ({audit_logger.count} records)")
    logger.info(f"DLQ saved to {dlq_path} ({dlq.size} entries)")

    return report


def load_tickets(path: str) -> list[RawTicket]:
    """Load tickets from a JSON file."""
    with open(path, "r") as f:
        data = json.load(f)
    return [RawTicket(**t) for t in data]


def main():
    """CLI entry point."""
    # Check for API key
    if not os.environ.get("GROQ_API_KEY"):
        print("ERROR: GROQ_API_KEY not set. Copy .env.example to .env and add your Groq API key from https://console.groq.com/keys")
        sys.exit(1)

    # Determine paths
    project_root = Path(__file__).parent.parent
    tickets_path = project_root / "data" / "tickets.json"
    audit_path = project_root / "audit_log.json"
    dlq_path = project_root / "dlq.json"

    if not tickets_path.exists():
        print(f"ERROR: Tickets file not found: {tickets_path}")
        sys.exit(1)

    # Load tickets
    tickets = load_tickets(str(tickets_path))
    logger.info(f"Loaded {len(tickets)} tickets from {tickets_path}")

    # Process
    report = asyncio.run(process_all_tickets(
        tickets,
        audit_path=str(audit_path),
        dlq_path=str(dlq_path),
    ))

    # Print report
    print(report.summary())


if __name__ == "__main__":
    main()
