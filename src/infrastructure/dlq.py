"""Dead letter queue for failed tickets."""

from __future__ import annotations

import asyncio
import json

import aiofiles

from ..agent.state import DLQEntry


class DeadLetterQueue:
    """Thread-safe DLQ that accumulates failed ticket entries."""

    def __init__(self):
        self._entries: list[DLQEntry] = []
        self._lock = asyncio.Lock()

    async def push(self, entry: DLQEntry) -> None:
        """Thread-safe append of a DLQ entry."""
        async with self._lock:
            self._entries.append(entry)

    async def dump(self, path: str) -> None:
        """Write all entries as a JSON array to file."""
        async with self._lock:
            async with aiofiles.open(path, "w") as f:
                data = [e.model_dump(mode="json") for e in self._entries]
                await f.write(json.dumps(data, indent=2, default=str))

    @property
    def size(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> list[DLQEntry]:
        return list(self._entries)
