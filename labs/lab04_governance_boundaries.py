#!/usr/bin/env python3
"""Lab 04 -- Finding every wall, on purpose.

Goal: deliberately hit each governance boundary and read what comes back.
An agent that has never seen a refusal will improvise past the first one.

Six boundaries: TTL, query budget, row budget, plane scope, classification,
and network. Each returns a `reason` and a `hint` that actually works.

Run:  python3 labs/lab04_governance_boundaries.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _harness import check, lab_platform, show, summary  # noqa: E402


def main() -> int:
    platform, call = lab_platform()

    print("Boundary 1 -- expiry. A TTL cannot be extended, by design.")
    short = call("sandbox_create", purpose="lab04: ttl", ttl_seconds=1)
    stored = platform.store.get_sandbox(short["id"])
    stored["expires_at"] = stored["created_at"] - 1  # simulate the clock running out
    platform.store.put_sandbox(short["id"], stored)
    expired = call("query_product", sandbox_id=short["id"], product_id="warehouse.finops.cost_by_service", limit=1)
    show("expired", expired)
    check(expired.get("reason") == "expired", "an expired sandbox refuses reads")
    check("new sandbox" in expired.get("hint", ""), "the hint says a TTL is not extendable")

    print("\nBoundary 2 -- query budget.")
    q = call("sandbox_create", purpose="lab04: queries", max_queries=2)
    for _ in range(2):
        call("query_product", sandbox_id=q["id"], product_id="warehouse.finops.cost_by_service", limit=1)
    over = call("query_product", sandbox_id=q["id"], product_id="warehouse.finops.cost_by_service", limit=1)
    check(over.get("reason") == "budget", "the third query is refused")
    check(call("sandbox_status", sandbox_id=q["id"])["usage"]["queries"] == 2, "the refused query was not charged")

    print("\nBoundary 3 -- row budget, checked BEFORE the backend is touched.")
    r = call("sandbox_create", purpose="lab04: rows", max_rows=10)
    big = call("query_product", sandbox_id=r["id"], product_id="warehouse.finops.cost_by_service", limit=100)
    check(big.get("reason") == "budget", "a request larger than the remaining budget is refused up front")
    check(call("sandbox_status", sandbox_id=r["id"])["usage"]["rows"] == 0, "nothing was read, so nothing was charged")
    small = call("query_product", sandbox_id=r["id"], product_id="warehouse.finops.cost_by_service", limit=10)
    check(small.get("row_count") == 10, "the hint (lower the limit) actually works")

    print("\nBoundary 4 -- plane scope.")
    scoped = call("sandbox_create", purpose="lab04: scope", allowed_planes=["warehouse"])
    out = call("query_product", sandbox_id=scoped["id"], product_id="bi.reporting.exec-cloud-spend", limit=1)
    check(out.get("reason") == "out_of_scope", "a BI product is out of scope for a warehouse-only sandbox")

    print("\nBoundary 5 -- classification, and the column-level escape hatch.")
    c = call("sandbox_create", purpose="lab04: classification")
    blocked = call("query_product", sandbox_id=c["id"], product_id="warehouse.finops.service_inventory", limit=2)
    check(blocked.get("reason") == "classification", "a PII column blocks the whole product")
    narrowed = call("query_product", sandbox_id=c["id"], product_id="warehouse.finops.service_inventory",
                    select=["service", "team"], limit=2)
    check(narrowed.get("row_count") == 2, "naming permitted columns works")
    via_filter = call("query_product", sandbox_id=c["id"], product_id="warehouse.finops.service_inventory",
                      select=["service"], where=[{"column": "owner_email", "op": "eq", "value": "x@y.z"}], limit=2)
    check(via_filter.get("reason") == "classification",
          "you cannot read a PII column sideways through a filter either")

    print("\nBoundary 6 -- network, denied by default.")
    n = call("sandbox_create", purpose="lab04: network")
    check(n["network"] == "none", "default is deny")
    allowed = call("sandbox_create", purpose="lab04: allowlist",
                   network="allowlist", network_allowlist=["status.example.invalid"])
    gate = platform.sandboxes.host_gate(platform.sandboxes.get(allowed["id"]))
    check(gate("status.example.invalid"), "the allowlisted host passes")
    check(gate("api.status.example.invalid"), "a subdomain passes")
    check(not gate("status.example.invalid.evil.net"), "a suffix attack does not")
    check(not gate("notstatus.example.invalid"), "a prefix attack does not")

    print("""
  Every refusal above is DATA -- {"error", "reason", "hint"} -- not an
  exception. That is deliberate: an agent that receives a structured
  refusal corrects itself; an agent that receives a stack trace retries
  the same call.

  The one refusal an agent must NOT "fix" is `classification`. Narrowing
  the read is correct. Creating a PII-cleared sandbox is a person's
  decision, not the agent's.
""")
    return summary()


if __name__ == "__main__":
    raise SystemExit(main())
