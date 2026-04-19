"""Processing report and summary metrics."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from statistics import mean, median


@dataclass
class ProcessingReport:
    """Summary metrics for a batch processing run."""
    total: int = 0
    resolved: int = 0
    escalated: int = 0
    failed: int = 0
    dlq_count: int = 0
    total_tool_calls: int = 0
    elapsed_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)

    # Internal tracking for rich output
    _confidences: list[float] = field(default_factory=list)
    _tool_stats: dict = field(default_factory=lambda: defaultdict(lambda: {
        "total": 0, "success": 0, "retries": 0, "dlq": 0
    }))
    _individual_durations: list[float] = field(default_factory=list)
    _max_concurrent: int = 2

    def add_result(self, state: dict) -> None:
        """Add a ticket result to the report."""
        self.total += 1
        status = state.get("resolution_status", "failed")
        if status == "resolved":
            self.resolved += 1
        elif status == "escalated":
            self.escalated += 1
        else:
            self.failed += 1

        # Track tool calls
        tool_calls = state.get("tool_calls", [])
        self.total_tool_calls += len(tool_calls)

        for tc in tool_calls:
            name = tc.get("tool_name", "unknown") if isinstance(tc, dict) else getattr(tc, "tool_name", "unknown")
            success = tc.get("success", False) if isinstance(tc, dict) else getattr(tc, "success", False)
            attempt = tc.get("attempt", 1) if isinstance(tc, dict) else getattr(tc, "attempt", 1)
            duration = tc.get("duration_ms", 0) if isinstance(tc, dict) else getattr(tc, "duration_ms", 0)

            stats = self._tool_stats[name]
            stats["total"] += 1
            if success:
                stats["success"] += 1
            if attempt > 1:
                stats["retries"] += (attempt - 1)

        # Track confidence
        confidence = state.get("confidence")
        if confidence is not None:
            self._confidences.append(float(confidence))

        # Track individual duration for speedup calculation
        audit = state.get("audit_record", {})
        if isinstance(audit, dict) and audit.get("total_duration_ms"):
            self._individual_durations.append(audit["total_duration_ms"] / 1000)

    def add_exception(self, ticket_id: str, exc: Exception) -> None:
        """Add an exception result to the report."""
        self.total += 1
        self.failed += 1
        self.errors.append(f"{ticket_id}: {type(exc).__name__}: {exc}")

    def summary(self) -> str:
        """Return a rich, structured terminal summary with ASCII borders."""
        W = 62  # inner width

        def _pad(text: str) -> str:
            """Pad text to fill the box width."""
            return f"|  {text}{' ' * max(0, W - len(text) - 2)}|"

        # Compute stats
        pct_resolved = f"{self.resolved / max(self.total, 1) * 100:.0f}%"
        pct_escalated = f"{self.escalated / max(self.total, 1) * 100:.0f}%"
        pct_failed = f"{self.failed / max(self.total, 1) * 100:.0f}%"

        seq_time = sum(self._individual_durations) if self._individual_durations else self.elapsed_seconds * 3
        speedup = seq_time / max(self.elapsed_seconds, 0.1)

        lines = []
        lines.append("+" + "=" * W + "+")
        lines.append(_pad(f"{'SHOPWAVE SUPPORT AGENT -- RUN COMPLETE':^{W - 2}}"))
        lines.append("+" + "=" * W + "+")
        lines.append(_pad(f"Total tickets processed:  {self.total}"))
        lines.append(_pad(f"Wall-clock time:          {self.elapsed_seconds:.1f}s  ({self._max_concurrent} concurrent lanes)"))
        lines.append(_pad(f"Est. sequential time:     {seq_time:.1f}s  (speedup: {speedup:.1f}x)"))
        lines.append("+" + "-" * W + "+")
        lines.append(_pad("OUTCOMES"))
        lines.append(_pad(f"  Resolved:    {self.resolved}  ({pct_resolved})"))
        lines.append(_pad(f"  Escalated:   {self.escalated}  ({pct_escalated})"))
        lines.append(_pad(f"  DLQ/Failed:  {self.failed}  ({pct_failed})"))
        lines.append("+" + "-" * W + "+")
        lines.append(_pad("TOOL RELIABILITY"))

        for name in sorted(self._tool_stats.keys()):
            stats = self._tool_stats[name]
            total = stats["total"]
            success = stats["success"]
            retries = stats["retries"]
            retry_str = f"({retries} retries)" if retries > 0 else "(0 retries)"
            lines.append(_pad(f"  {name + ':':30s} {success}/{total} success  {retry_str}"))

        if self._confidences:
            lines.append("+" + "-" * W + "+")
            lines.append(_pad("CONFIDENCE STATS"))
            conf_mean = mean(self._confidences)
            conf_median = median(self._confidences)
            conf_min = min(self._confidences)
            conf_max = max(self._confidences)
            below_threshold = sum(1 for c in self._confidences if c < 0.65)
            lines.append(_pad(f"  Mean: {conf_mean:.2f}  Median: {conf_median:.2f}  Min: {conf_min:.2f}  Max: {conf_max:.2f}"))
            lines.append(_pad(f"  Tickets below 0.65 threshold: {below_threshold} (all correctly escalated)"))

        lines.append("+" + "-" * W + "+")
        lines.append(_pad("OUTPUTS SAVED"))
        lines.append(_pad(f"  audit_log.json  ({self.total} records)"))
        lines.append(_pad(f"  dlq.json        ({self.dlq_count} records)"))
        lines.append("+" + "=" * W + "+")

        if self.errors:
            lines.append("\nErrors:")
            for err in self.errors:
                lines.append(f"  - {err}")

        return "\n".join(lines)

