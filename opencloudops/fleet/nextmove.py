"""Next moves: what the managing partner should look at, in order.

Rules, not a model. Every move names the evidence it came from (an event, a
repo fact checked on a date), so a recommendation can be checked against its
source before anyone acts on it. Ranking is by who is blocked: a bot waiting
on a person outranks a bot that went quiet, which outranks housekeeping.
"""

from __future__ import annotations

from typing import Any

from opencloudops.fleet.firm import Inventory
from opencloudops.fleet.state import FleetState

_PRIORITY = {"decide": 1, "unblock": 2, "investigate": 3, "wire-up": 4, "tidy": 5}


def next_moves(state: FleetState, inventory: Inventory | None = None, limit: int = 8) -> list[dict[str, Any]]:
    moves: list[dict[str, Any]] = []
    rows = state.bot_status()

    for row in rows:
        ev = row.get("last_event")
        if row["status"] == "needs-you" and ev:
            moves.append({
                "type": "decide" if ev["kind"] in {"needs_human", "escalated"} else "unblock",
                "title": f"{row['name']}: {ev['summary'] or ev['kind'].replace('_', ' ')}",
                "why": f"Its last event was {ev['kind']} at {ev['ts']}; it waits on a person.",
                "evidence": ev.get("url") or f"event from {row['id']}",
                "bot": row["id"],
            })
        elif row["status"] == "silent":
            moves.append({
                "type": "investigate",
                "title": f"{row['name']} has gone quiet",
                "why": (f"Expected a report every {row['heartbeat_minutes']} min; "
                        f"last one was {row.get('age_minutes')} min ago."),
                "evidence": f"last event {ev['kind'] if ev else 'none'}",
                "bot": row["id"],
            })

    never = [r for r in rows if r["status"] == "never-reported"]
    if never:
        names = ", ".join(r["name"] for r in never[:4]) + ("…" if len(never) > 4 else "")
        moves.append({
            "type": "wire-up",
            "title": f"Connect {len(never)} built bots to Mission Control",
            "why": f"These have code but have never reported, so the console cannot show what they do: {names}.",
            "evidence": "firm/firm.yaml roster vs. events received",
            "bot": None,
        })

    if inventory:
        for fact in inventory.repos:
            if fact.action and (fact.urgent or fact.state in {"broken", "dead", "stub"}):
                moves.append({
                    "type": "decide" if fact.urgent or fact.state == "broken" else "tidy",
                    "title": f"{fact.repo}: {fact.action}",
                    "why": fact.evidence,
                    "evidence": f"repo check on {inventory.checked}",
                    "bot": None,
                })

    moves.sort(key=lambda m: _PRIORITY[m["type"]])
    return moves[:limit]
