from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


class TraceSink(Protocol):
    async def emit(self, event: dict[str, Any]) -> None: ...


class NullTraceSink:
    async def emit(self, event: dict[str, Any]) -> None:
        return None


@dataclass
class MemoryTraceSink:
    events: list[dict[str, Any]] = field(default_factory=list)

    async def emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class JsonlTraceSink:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # The sink may be constructed before an ASGI server creates its event loop,
        # or reused across multiple asyncio.run() calls. A threading lock is not
        # bound to an event loop and keeps concurrent append operations atomic.
        self._lock = threading.Lock()

    async def emit(self, event: dict[str, Any]) -> None:
        row = {"timestamp": datetime.now(timezone.utc).isoformat(), **event}
        line = json.dumps(row, default=str, ensure_ascii=False) + "\n"
        await asyncio.to_thread(_append_locked, self._lock, self.path, line)


def _append(path: Path, line: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)


def _append_locked(lock: threading.Lock, path: Path, line: str) -> None:
    with lock:
        _append(path, line)
