"""Append-only JSONL audit trail.

Every agent stage emits an event here. This is not logging for humans --
it is the record that makes an agent's decision reconstructable after the
fact, which is the only reason it is safe to let an agent near infrastructure
at all. Stages that produce no event are treated as a bug by
``tests/test_agents.py``.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_LOG = Path(os.environ.get("OCC_AUDIT_LOG", "logs/audit.jsonl"))

_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditLog:
    """A single append-only JSONL sink, plus an in-memory mirror so callers
    can return the trace in an API response without re-reading the file."""

    def __init__(self, path: Path | str | None = DEFAULT_LOG) -> None:
        self.path = Path(path) if path is not None else None
        self.events: list[dict[str, Any]] = []

    def log(self, stage: str, status: str = "ok", **fields: Any) -> dict[str, Any]:
        event = {"ts": _now(), "stage": stage, "status": status, **fields}
        self.events.append(event)
        if self.path is not None:
            with _lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(event, default=str) + "\n")
        return event

    def tail(self, last: int = 50) -> list[dict[str, Any]]:
        if self.path is None or not self.path.exists():
            return self.events[-last:]
        lines = self.path.read_text(encoding="utf-8").splitlines()[-last:]
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out


def memory_log() -> AuditLog:
    """An audit log that writes nowhere -- for tests and dry runs."""
    return AuditLog(path=None)
