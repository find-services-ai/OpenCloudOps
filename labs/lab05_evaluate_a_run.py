#!/usr/bin/env python3
"""Lab 05 -- Grading the research, not the answer.

Goal: see that the rubric distinguishes a disciplined run from a sloppy
one. A rubric that only ever passes is decoration.

Two runs, same mesh:
  A. Scoped sandbox, adequate budget, closed afterwards.
  B. Wide-open scope, starved budget, left running.

Both produce a report. Only one is trustworthy, and the rubric says which
and why.

Run:  python3 labs/lab05_evaluate_a_run.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evaluation"))

from _harness import check, lab_platform, sandbox_research_path, summary  # noqa: E402

sys.path.insert(0, str(sandbox_research_path()))

from evaluate import Evaluator, render  # noqa: E402

QUESTION = "Why is weekly cloud cost rising and which incidents explain it?"


def main() -> int:
    platform, call = lab_platform()
    from src.research import ResearchEngine  # noqa: PLC0415

    engine = ResearchEngine(platform)

    print("=" * 70)
    print("RUN A -- scoped, budgeted, closed")
    print("=" * 70)
    good = call(
        "sandbox_create", purpose="lab05: disciplined run",
        ttl_seconds=900, max_queries=25, max_rows=15_000,
        allowed_planes=["warehouse", "lake", "bi"],
    )
    plan_a = engine.plan(good["id"], QUESTION, max_steps=4)
    call("research_run", plan_id=plan_a.id, rows_per_step=40)
    call("sandbox_destroy", sandbox_id=good["id"])
    eval_a = Evaluator(platform).evaluate(plan_a.id)
    print(render(eval_a))

    print("\n" + "=" * 70)
    print("RUN B -- every plane, starved budget, left open")
    print("=" * 70)
    sloppy = call(
        "sandbox_create", purpose="lab05: sloppy run",
        ttl_seconds=900, max_queries=3, max_rows=60,
    )
    plan_b = engine.plan(sloppy["id"], QUESTION, max_steps=4)
    call("research_run", plan_id=plan_b.id, rows_per_step=40)
    eval_b = Evaluator(platform).evaluate(plan_b.id)
    print(render(eval_b))

    print("\n" + "=" * 70)
    print("WHAT THE RUBRIC CAUGHT")
    print("=" * 70)
    check(eval_a["verdict"] == "trustworthy", "the disciplined run is graded trustworthy")
    check(eval_b["score"] < eval_a["score"], "the sloppy run scores lower than the disciplined one")

    failed_b = {c["id"] for c in eval_b["criteria"] if not c["passed"]}
    check("budget_discipline" in failed_b, "the starved budget was caught")
    check("sandbox_closed" in failed_b, "the still-open sandbox was caught")
    check("scope_minimality" in failed_b or eval_b["score"] < 0.9,
          "unjustified all-plane scope cost it points")

    print(f"""
  Run A: {eval_a['points']} -> {eval_a['verdict']}
  Run B: {eval_b['points']} -> {eval_b['verdict']}

  Note what the rubric does NOT grade: whether the conclusion is correct.
  No rubric can settle that. It grades whether the run was scoped, cited,
  reproducible and honest about its gaps -- because a run that fails those
  is not trustworthy even when its conclusion happens to be right.

  Note also `disqualifying` in rubric.yaml. Two criteria cap the verdict at
  untrustworthy however good the rest of the score is: an uncited finding,
  and a restricted column in the evidence. Neither is fixable by re-running.
""")
    return summary()


if __name__ == "__main__":
    raise SystemExit(main())
