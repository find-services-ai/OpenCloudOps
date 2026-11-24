"""Post a handful of clearly-labelled demo events to a local Mission Control.

    OCC_INGEST_TOKEN=dev uvicorn opencloudops.api:app --port 8081 &
    OCC_INGEST_TOKEN=dev python scripts/fleet_demo.py http://127.0.0.1:8081
    open http://127.0.0.1:8081/console

Every event is sent with "synthetic": true, and the console marks it "demo",
so a demo run can never be mistaken for a bot really reporting.
Standard library only.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import UTC, datetime, timedelta

EVENTS = [
    ("gos-harness", "task_started", "Running the eval suite for ember", 2),
    ("occ-sre-observer", "heartbeat", "Burn-rate check: all SLOs inside budget", 4),
    ("occ-iac-builder", "task_finished", "Rendered Terraform for reference-web-service (dev)", 30),
    ("sow-architect", "needs_human", "SOW draft for the data-platform pilot is ready for your approval", 12),
    ("surplus-engine", "heartbeat", "Nightly forecast back-test finished", 200),
    ("occ-release-executor", "heartbeat", "No change records pending", 400),
]


def main(base: str) -> int:
    token = os.environ.get("OCC_INGEST_TOKEN", "")
    if not token:
        print("set OCC_INGEST_TOKEN to the value the server was started with", file=sys.stderr)
        return 2
    now = datetime.now(UTC)
    for agent, kind, summary, minutes_ago in EVENTS:
        body = {"agent": agent, "kind": kind, "summary": summary, "synthetic": True,
                "ts": (now - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")}
        req = urllib.request.Request(
            base.rstrip("/") + "/fleet/events", data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(resp.status, agent, kind)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8081"))
