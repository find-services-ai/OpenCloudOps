#!/usr/bin/env python3
"""Lab 03 -- Blast radius before root cause.

Goal: answer "if we change this, what breaks?" across planes, and notice
that the answer is only as good as the lineage someone declared.

The realistic CloudOps framing: you are about to change the schema of a
warehouse cost table. Who finds out the hard way?

Run:  python3 labs/lab03_blast_radius.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _harness import check, lab_platform, show, summary  # noqa: E402

TARGET = "warehouse.finops.cost_by_service"


def main() -> int:
    _platform, call = lab_platform()

    print("Step 1 -- who consumes this product?")
    down = call("catalog_lineage", product_id=TARGET, direction="down", depth=3)
    show("lineage (down)", down)
    consumers = [e["to"] for e in down["edges"]]
    check(bool(consumers), "the cost table has at least one declared consumer")
    check(
        any(c.startswith("bi.") for c in consumers),
        "a BI dashboard is downstream -- the change is visible to the business, not just to engineers",
    )

    print("\nStep 2 -- who owns what breaks?")
    for consumer in consumers:
        card = call("catalog_describe", product_id=consumer)
        print(f"  {consumer:40s} owner={card['owner']}")
        check(card["owner"] != "unassigned", f"{consumer} has a named owner to notify")

    print("\nStep 3 -- the other direction. What does the dashboard depend on?")
    up = call("catalog_lineage", product_id="bi.reporting.reliability-review", direction="up")
    show("lineage (up)", {"edges": up["edges"]})
    check(len(up["edges"]) == 2, "the reliability dashboard declares both of its lake sources")

    print("\nStep 4 -- the limit of the answer.")
    orphan = call("catalog_lineage", product_id="lake.cloudops.deploy_events", direction="up")
    check(orphan["found"] and not orphan["edges"], "deploy_events declares no upstream -- and none is inferred")
    print("""
  The catalog reports DECLARED lineage. `deploy_events` shows no upstream
  not because it has none, but because nothing declared one. A blast-radius
  answer is exactly as complete as the declarations behind it.

  This is the honest failure mode of every lineage tool, and it is why
  `catalog_lineage` returns edges rather than a confidence score: a number
  would imply the tool knows what it does not.
""")

    print("Think about: your change is approved and the lineage shows one dashboard.")
    print("What would you check before believing that is the whole blast radius?")
    return summary()


if __name__ == "__main__":
    raise SystemExit(main())
