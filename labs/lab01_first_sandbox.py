#!/usr/bin/env python3
"""Lab 01 -- Your first governed research run.

Goal: run the full loop and understand what each step bought you.

    sandbox_create -> research_plan -> research_run -> research_report

What to notice:
  * The plan lists WHICH products it will read and WHY, before reading any.
  * The report names the plane it could not cover. That gap is the product.
  * The sandbox ledger shows exactly what the run spent.

Run:  python3 labs/lab01_first_sandbox.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _harness import check, lab_platform, show, summary  # noqa: E402

QUESTION = "Why is weekly cloud cost rising, and which incidents and deploys explain it?"


def main() -> int:
    _platform, call = lab_platform()

    sandbox = call(
        "sandbox_create",
        purpose="lab01: cloud cost investigation",
        ttl_seconds=900,
        max_queries=20,
        max_rows=10_000,
    )
    show("sandbox_create", {k: sandbox[k] for k in ("id", "budget", "allowed_planes", "allow_classifications")})
    check(sandbox["network"] == "none", "network is denied by default -- you did not have to ask for that")

    plan = call("research_plan", sandbox_id=sandbox["id"], question=QUESTION, max_steps=4)
    show("research_plan", plan)
    check(bool(plan["steps"]), "the plan selected at least one data product")
    check(
        all("match score" in s["rationale"] for s in plan["steps"]),
        "every step states the catalog match that selected it (retrieval is inspectable)",
    )
    check(bool(plan["gaps"]), "the plan names what it could NOT cover")

    run = call("research_run", plan_id=plan["id"], rows_per_step=40)
    show("research_run", {k: run[k] for k in ("state", "steps_ok", "steps_failed")})

    report = call("research_report", plan_id=plan["id"])
    show("research_report", report)
    check(
        all(f["digest"].startswith("sha256:") for f in report["findings"]),
        "every finding carries an evidence digest",
    )
    check(len(report["planes_covered"]) >= 2, "the answer draws on more than one mesh plane")

    status = call("sandbox_status", sandbox_id=sandbox["id"])
    show("sandbox_status", {"usage": status["usage"], "budget_remaining": status["budget_remaining"]})
    check(status["usage"]["queries"] == run["steps_ok"], "the ledger charged exactly one query per successful step")

    call("sandbox_destroy", sandbox_id=sandbox["id"])
    print("\nThink about: the report's confidence is COVERAGE, not statistics.")
    print("What would you have to do to turn it into a statistical claim?")
    return summary()


if __name__ == "__main__":
    raise SystemExit(main())
