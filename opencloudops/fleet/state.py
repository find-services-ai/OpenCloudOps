"""Live fleet state: events in, per-bot status out.

The event schema is the one the gos harness already emits
(``ethos/harness/gos/events.py``: kind, summary, repo, ref, url, agent, ts),
so pointing ``GOS_MC_URL`` at this service's ``/fleet/events`` needs no change
on the harness side. Four kinds are added for bots that are not CI steps:
``heartbeat``, ``task_started``, ``task_finished`` and ``needs_human``.

Status is derived, never reported. A bot cannot claim to be healthy; it can
only report what it did, and the console works out from the clock whether
that is recent enough.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from opencloudops.fleet.firm import Firm

HARNESS_KINDS = (
    "intake", "spec_approved", "build_started", "pr_opened", "ci_passed", "ci_failed",
    "tribunal_pass", "tribunal_blocked", "sme_fix", "gate_passed", "gate_failed",
    "deployed", "escalated", "probe",
)
BOT_KINDS = ("heartbeat", "task_started", "task_finished", "needs_human")
EVENT_KINDS = HARNESS_KINDS + BOT_KINDS

# Kinds that mean a person has to do something before the bot can go on.
ATTENTION_KINDS = {"needs_human", "escalated", "gate_failed", "tribunal_blocked", "ci_failed"}
# Kinds that mean the bot is in the middle of something.
BUSY_KINDS = {"task_started", "build_started", "intake", "spec_approved", "sme_fix"}

MAX_EVENTS = 2000


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_ts(value: str | None) -> datetime:
    if not value:
        return _now()
    ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


class FleetState:
    """In-memory event store with optional append-only JSONL persistence."""

    def __init__(self, firm: Firm, log_path: Path | None = None, clock=_now):
        self.firm = firm
        self._clock = clock
        self._events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
        self._lock = threading.Lock()
        self._log = log_path
        if log_path and log_path.exists():
            for line in log_path.read_text().splitlines():
                if line.strip():
                    try:
                        self._events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue  # a torn last line must not take the console down

    # -- ingest -------------------------------------------------------------

    def record(self, event: dict[str, Any]) -> dict[str, Any]:
        kind = event.get("kind")
        if kind not in EVENT_KINDS:
            raise ValueError(f"unknown event kind {kind!r}")
        agent = (event.get("agent") or "").strip()
        clean = {
            "kind": kind,
            "summary": str(event.get("summary", "")).strip()[:280],
            "agent": agent,
            "repo": str(event.get("repo", ""))[:200],
            "ref": str(event.get("ref", ""))[:200],
            "url": str(event.get("url", ""))[:500] if str(event.get("url", "")).startswith("https://") else "",
            "ts": _parse_ts(event.get("ts")).isoformat(timespec="seconds"),
            "received": self._clock().isoformat(timespec="seconds"),
            "synthetic": bool(event.get("synthetic", False)),
            "known_bot": self.firm.bot(agent) is not None,
        }
        with self._lock:
            self._events.append(clean)
            if self._log:
                self._log.parent.mkdir(parents=True, exist_ok=True)
                with self._log.open("a") as fh:
                    fh.write(json.dumps(clean) + "\n")
        return clean

    def events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)[-limit:][::-1]

    # -- derived status -----------------------------------------------------

    def _last_by_bot(self) -> dict[str, dict[str, Any]]:
        last: dict[str, dict[str, Any]] = {}
        with self._lock:
            for ev in self._events:
                if ev.get("agent"):
                    last[ev["agent"]] = ev
        return last

    def bot_status(self) -> list[dict[str, Any]]:
        now = self._clock()
        last = self._last_by_bot()
        out = []
        for bot in self.firm.bots:
            ev = last.get(bot.id)
            row: dict[str, Any] = {**bot.model_dump(), "last_event": ev}
            if bot.maturity == "stub":
                row["status"] = "stub"
                row["doing"] = "Not built yet"
            elif ev is None:
                row["status"] = "never-reported"
                row["doing"] = "Has not reported to Mission Control"
            else:
                age_min = (now - _parse_ts(ev["ts"])).total_seconds() / 60
                row["age_minutes"] = round(age_min, 1)
                if ev["kind"] in ATTENTION_KINDS:
                    row["status"] = "needs-you"
                elif age_min > bot.heartbeat_minutes * 3:
                    row["status"] = "silent"
                elif age_min > bot.heartbeat_minutes * 1.5:
                    row["status"] = "late"
                elif ev["kind"] in BUSY_KINDS:
                    row["status"] = "working"
                else:
                    row["status"] = "idle"
                row["doing"] = ev["summary"] or ev["kind"]
            out.append(row)
        return out

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.bot_status():
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        return counts
