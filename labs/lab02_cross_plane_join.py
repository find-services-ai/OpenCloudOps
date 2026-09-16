#!/usr/bin/env python3
"""Lab 02 -- Unifying the mesh without moving the data.

Goal: answer a question no single plane can answer, and see why that is the
whole point of a catalog rather than a central warehouse.

Incidents live in object storage. Service ownership lives in the warehouse.
They share no database and no credentials. `unified_query` joins them on
`service` -- and each source is still governed separately.

Run:  python3 labs/lab02_cross_plane_join.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _harness import check, lab_platform, show, summary  # noqa: E402


def main() -> int:
    _platform, call = lab_platform()

    print("Step 1 -- find the products. Never query blind.")
    hits = call("catalog_search", query="incidents service ownership team", limit=5)["results"]
    show("catalog_search", [{"id": h["id"], "plane": h["plane"], "score": h["match_score"]} for h in hits])
    check(len({h["plane"] for h in hits}) >= 2, "the search reached more than one plane")

    print("\nStep 2 -- read the schema before reading the rows.")
    card = call("catalog_describe", product_id="warehouse.finops.service_inventory")
    show("catalog_describe", {k: card[k] for k in ("id", "classifications", "contains_restricted", "owner")})
    check(card["contains_restricted"], "service_inventory carries a PII column -- the catalog says so up front")

    sandbox = call("sandbox_create", purpose="lab02: incidents by owning team", max_rows=20_000)

    print("\nStep 3 -- the naive read is refused, and the refusal is useful.")
    refused = call("query_product", sandbox_id=sandbox["id"], product_id="warehouse.finops.service_inventory")
    show("refusal", refused)
    check(refused.get("reason") == "classification", "reading every column is refused on the PII column")
    check("select" in refused.get("hint", ""), "the refusal tells you how to proceed")

    print("\nStep 4 -- join across planes with an explicit select.")
    joined = call(
        "unified_query",
        sandbox_id=sandbox["id"],
        sources=[
            {"product_id": "lake.cloudops.incidents",
             "select": ["incident_id", "service", "severity", "root_cause"], "limit": 60},
            {"product_id": "warehouse.finops.service_inventory",
             "select": ["service", "team", "tier"], "limit": 10},
        ],
        join_on="service",
        limit=8,
    )
    show("unified_query", joined)
    check(joined["planes_unified"] == ["lake", "warehouse"], "one result set spanning two planes")
    check(all("owner_email" not in r for r in joined["rows"]), "no PII column reached the result")
    check(all(len(r["_sources"]) == 2 for r in joined["rows"]), "every row records which products it came from")

    print("\nStep 5 -- unification does not widen scope.")
    narrow = call("sandbox_create", purpose="lab02: warehouse only", allowed_planes=["warehouse"])
    blocked = call(
        "unified_query",
        sandbox_id=narrow["id"],
        sources=[
            {"product_id": "warehouse.finops.cost_by_service", "select": ["week"], "limit": 2},
            {"product_id": "lake.cloudops.incidents", "select": ["incident_id"], "limit": 2},
        ],
    )
    check(blocked.get("reason") == "out_of_scope", "a lake source inside a warehouse-only sandbox is still refused")

    print("\nThink about: what would centralising this data into one warehouse have cost,")
    print("and what would it have bought that the catalog does not?")
    return summary()


if __name__ == "__main__":
    raise SystemExit(main())
