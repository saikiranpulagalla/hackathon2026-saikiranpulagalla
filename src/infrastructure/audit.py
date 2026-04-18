"""Audit logger — thread-safe, in-memory accumulation with single JSON array write."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import aiofiles

from ..agent.state import AuditRecord


class AuditLogger:
    """Accumulates audit records in memory, writes once as a JSON array."""

    def __init__(self, path: str):
        self._path = path
        self._lock = asyncio.Lock()
        self._records: list[AuditRecord] = []

    async def write(self, record: AuditRecord) -> None:
        """Append record to in-memory list (thread-safe)."""
        async with self._lock:
            self._records.append(record)

    async def save(self) -> None:
        """Write all records as a JSON array to file."""
        async with self._lock:
            async with aiofiles.open(self._path, "w") as f:
                data = [_serialize_record(r) for r in self._records]
                await f.write(json.dumps(data, indent=2, default=str))

    @property
    def count(self) -> int:
        return len(self._records)


def _serialize_record(record: AuditRecord) -> dict[str, Any]:
    """Serialize an AuditRecord to a JSON-safe dict."""
    return record.model_dump(mode="json")
