#!/usr/bin/env python3
"""Grade one research run against evaluation/rubric.yaml.

    python3 evaluation/evaluate.py --demo         # run a research question, then grade it
    python3 evaluation/evaluate.py --plan-id plan_... --db ./research.db

This is the "evaluate the on-demand research sandbox" half of the brief.
It grades the RUN -- scope, citations, reproducibility, honesty about gaps --
not the conclusion, which no rubric can settle.

Reproducibility is checked by re-executing each evidence query in a fresh
audit sandbox scoped to the same planes. The auditor spends its own budget;
it does not quietly reuse the subject's.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "labs"))

import yaml  # noqa: E402

from _harness import lab_platform, sandbox_research_path  # noqa: E402

RUBRIC_PATH = Path(__file__).parent / "rubric.yaml"
ALL_PLANES = {"warehouse", "lake", "bi", "api"}


def load_rubric(path: Path = RUBRIC_PATH) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class Evaluator:
    def __init__(self, platform, rubric: dict[str, Any] | None = None) -> None:
        self.platform = platform
        self.rubric = rubric or load_rubric()

    # -- individual criteria ---------------------------------------------

    def _evidence_completeness(self, report, plan, _sandbox) -> tuple[bool, str]:
        """Checked in both directions.

        Forward: every finding resolves to a ledger record. Backward: every
        step the run reported as successful produced a finding. Only checking
        forward passes a run that lost evidence between execution and report,
        because the finding disappears along with the record it cited.
        """
        if not report["findings"]:
            return False, "the report contains no findings at all"

        missing = [
            f["evidence_id"] for f in report["findings"]
            if self.platform.store.get_evidence(f["evidence_id"]) is None
        ]
        if missing:
            return False, f"{len(missing)} finding(s) cite an evidence id that is not in the ledger: {missing}"

        succeeded = {s["id"] for s in plan["steps"] if s.get("status") == "ok"}
        cited_steps = {
            ev["step_id"] for ev in self.platform.store.evidence_for_plan(plan["id"])
        }
        orphaned = sorted(succeeded - cited_steps)
        if orphaned:
            return False, (
                f"{len(orphaned)} step(s) ran successfully but have no evidence in the ledger: "
                f"{orphaned} -- evidence was lost between execution and reporting"
            )
        return True, (
            f"all {len(report['findings'])} finding(s) resolve to ledger records, "
            f"and all {len(succeeded)} successful step(s) are represented"
        )

    def _no_restricted_in_evidence(self, _report, plan, _sandbox) -> tuple[bool, str]:
        offenders: list[str] = []
        for ev in self.platform.store.evidence_for_plan(plan["id"]):
            product = self.platform.catalog.get(ev["product_id"]) or {}
            restricted = {
                c["name"] for c in product.get("columns", [])
                if c.get("classification") in ("pii", "restricted")
            }
            for sample in ev["sample"]:
                leaked = restricted & set(sample)
                if leaked:
                    offenders.append(f"{ev['id']}:{sorted(leaked)}")
        if offenders:
            return False, f"restricted columns present in evidence samples: {offenders}"
        return True, "no pii/restricted column reached an evidence sample"

    def _evidence_reproducible(self, _report, plan, sandbox) -> tuple[bool, str]:
        evidence = self.platform.store.evidence_for_plan(plan["id"])
        if not evidence:
            return False, "no evidence to reproduce"

        audit = self.platform.sandboxes.create(
            f"audit of {plan['id']}",
            ttl_seconds=300,
            max_queries=len(evidence) + 2,
            max_rows=max(1, sum(e["row_count"] for e in evidence)) + 10,
            allowed_planes=sandbox["allowed_planes"],
            allow_classifications=sandbox["allow_classifications"],
        )
        mismatched: list[str] = []
        errors: list[str] = []
        for ev in evidence:
            select = _select_from_query(ev["query"])
            try:
                result = self.platform.query(
                    audit.id, ev["product_id"], select=select, limit=max(1, ev["row_count"])
                )
            except Exception as e:  # noqa: BLE001 - an unreadable product is a finding, not a crash
                errors.append(f"{ev['id']}: {type(e).__name__}: {e}")
                continue
            if result["digest"] != ev["digest"]:
                mismatched.append(ev["id"])
        self.platform.sandboxes.destroy(audit.id)

        if errors:
            return False, f"could not re-execute {len(errors)} evidence quer(ies): {errors[:2]}"
        if mismatched:
            return False, f"{len(mismatched)} digest(s) did not reproduce: {mismatched} (data changed, or the read was non-deterministic)"
        return True, f"all {len(evidence)} evidence digest(s) reproduced exactly"

    def _gaps_declared(self, report, _plan, sandbox) -> tuple[bool, str]:
        evidenced = set(report["planes_covered"])
        unreached = set(sandbox["allowed_planes"]) - evidenced
        if not unreached:
            return True, "the run covered every plane its sandbox allowed; nothing to declare"
        if not report["gaps"]:
            return False, f"planes {sorted(unreached)} produced no evidence and the report declares no gaps"
        mentioned = [p for p in unreached if any(p in g for g in report["gaps"])]
        if set(mentioned) != unreached:
            return False, f"gaps declared, but {sorted(unreached - set(mentioned))} not mentioned"
        return True, f"all unreached planes named in the report: {sorted(unreached)}"

    def _plane_coverage(self, report, _plan, _sandbox) -> tuple[bool, str]:
        n = len(report["planes_covered"])
        return n >= 2, f"evidence drawn from {n} plane(s): {report['planes_covered']}"

    def _scope_minimality(self, report, _plan, sandbox) -> tuple[bool, str]:
        """Narrow scope passes outright. Wide scope passes only if the breadth
        was actually used.

        An earlier version of this check let a wide sandbox pass whenever the
        report listed any gap at all -- which the planner always produces, so
        the escape hatch was free. Requiring that every allowed plane produced
        evidence is the version that cannot be satisfied by accident.
        """
        allowed = set(sandbox["allowed_planes"])
        if allowed < ALL_PLANES or sandbox["allowed_domains"]:
            return True, f"sandbox scoped to {sorted(allowed)}" + (
                f", domains {sandbox['allowed_domains']}" if sandbox["allowed_domains"] else ""
            )
        unused = allowed - set(report["planes_covered"])
        if not unused:
            return True, "scope was every plane, and every plane produced evidence -- the breadth was used"
        return False, (
            f"sandbox allowed all {len(allowed)} planes but drew evidence from "
            f"{len(report['planes_covered'])}; {sorted(unused)} went unused"
        )

    def _budget_discipline(self, report, _plan, sandbox) -> tuple[bool, str]:
        """Headroom alone is not discipline.

        A run can end with spare queries and still have lost steps to the row
        budget -- it simply stopped costing once it started failing. Checking
        the failed steps for a budget refusal catches that; checking only the
        remaining balance rewards it.
        """
        starved = [f for f in report["failed_steps"] if "budget" in f["error"].lower()]
        if starved:
            return False, (
                f"{len(starved)} step(s) were refused for budget, so the answer is "
                f"missing data the plan asked for: {[f['product_id'] for f in starved]}"
            )
        budget, usage = sandbox["budget"], sandbox["usage"]
        q_left = budget["max_queries"] - usage["queries"]
        r_left = budget["max_rows"] - usage["rows"]
        if q_left <= 0 or r_left <= 0:
            return False, f"budget exhausted (queries left {q_left}, rows left {r_left})"
        return True, f"finished with {q_left} quer(ies) and {r_left} row(s) of headroom"

    def _sandbox_closed(self, _report, _plan, sandbox) -> tuple[bool, str]:
        state = sandbox["state"]
        return state in ("destroyed", "expired"), f"sandbox state at evaluation: {state}"

    # -- driver -----------------------------------------------------------

    def evaluate(self, plan_id: str) -> dict[str, Any]:
        plan = self.platform.store.get_plan(plan_id)
        if plan is None:
            raise ValueError(f"no such plan: {plan_id}")

        from src.research import ResearchEngine  # imported here: path is set by _harness

        report = ResearchEngine(self.platform).report(plan_id)
        # Read the sandbox AFTER the audit-relevant state settles, so
        # sandbox_closed grades the real end state.
        sandbox = self.platform.sandboxes.status(plan["sandbox_id"])

        checks = {c["id"]: getattr(self, f"_{c['id']}") for c in self.rubric["criteria"]}
        results = []
        earned = achievable = 0.0
        for criterion in self.rubric["criteria"]:
            weight = float(criterion["weight"])
            achievable += weight
            try:
                passed, detail = checks[criterion["id"]](report, plan, sandbox)
            except Exception as e:  # noqa: BLE001 - a broken check fails the criterion, not the run
                passed, detail = False, f"check errored: {type(e).__name__}: {e}"
            earned += weight if passed else 0.0
            results.append({
                "id": criterion["id"],
                "weight": weight,
                "passed": passed,
                "detail": detail,
                "description": criterion["description"],
            })

        score = earned / achievable if achievable else 0.0
        failed_ids = {r["id"] for r in results if not r["passed"]}
        disqualified = sorted(failed_ids & set(self.rubric.get("disqualifying", [])))

        if disqualified:
            verdict = {"label": "untrustworthy", "note": "Do not act on this report."}
        else:
            verdict = next(
                ({"label": v["label"], "note": v["note"]}
                 for v in self.rubric["verdicts"] if score >= v["min"]),
                {"label": "untrustworthy", "note": "Do not act on this report."},
            )

        return {
            "plan_id": plan_id,
            "question": report["question"],
            "score": round(score, 3),
            "points": f"{earned:g}/{achievable:g}",
            "verdict": verdict["label"],
            "verdict_note": verdict["note"],
            "disqualified_by": disqualified,
            "criteria": results,
        }


def _select_from_query(query: str) -> list[str] | None:
    if not query.upper().startswith("SELECT "):
        return None
    cols = [c.strip() for c in query[len("SELECT "):].split(" LIMIT ")[0].split(",") if c.strip()]
    return cols or None


def render(evaluation: dict[str, Any]) -> str:
    lines = [
        f"Research run evaluation -- {evaluation['plan_id']}",
        f"Question: {evaluation['question']}",
        "",
        f"  {'CRITERION':<28} {'W':>2}  RESULT",
    ]
    for c in evaluation["criteria"]:
        lines.append(f"  {c['id']:<28} {c['weight']:>2g}  [{'PASS' if c['passed'] else 'FAIL'}] {c['detail']}")
    lines += [
        "",
        f"Score: {evaluation['points']} ({evaluation['score']:.0%})",
        f"Verdict: {evaluation['verdict'].upper()} -- {evaluation['verdict_note']}",
    ]
    if evaluation["disqualified_by"]:
        lines.append(f"Disqualifying failure(s): {evaluation['disqualified_by']}")
    return "\n".join(lines)


def _demo() -> dict[str, Any]:
    """Run a well-formed research question, then grade it."""
    sys.path.insert(0, str(sandbox_research_path()))
    platform, call = lab_platform()
    from src.research import ResearchEngine  # noqa: PLC0415

    sandbox = call(
        "sandbox_create",
        purpose="evaluation demo: cost and reliability",
        ttl_seconds=900, max_queries=25, max_rows=15_000,
        allowed_planes=["warehouse", "lake", "bi"],
    )
    plan = ResearchEngine(platform).plan(
        sandbox["id"], "Why is weekly cloud cost rising and which incidents explain it?", max_steps=4
    )
    call("research_run", plan_id=plan.id, rows_per_step=40)
    evaluation = Evaluator(platform).evaluate(plan.id)
    call("sandbox_destroy", sandbox_id=sandbox["id"])
    # Re-grade after closing, so sandbox_closed reflects a completed lifecycle.
    return Evaluator(platform).evaluate(plan.id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Grade a research run against the rubric.")
    parser.add_argument("--demo", action="store_true", help="run a fresh research question, then grade it")
    parser.add_argument("--plan-id", help="grade an existing plan")
    parser.add_argument("--db", help="research database (default: the labs' in-memory mesh)")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = parser.parse_args(argv)

    if args.demo:
        evaluation = _demo()
    elif args.plan_id:
        platform, _ = lab_platform(db=args.db or ":memory:")
        evaluation = Evaluator(platform).evaluate(args.plan_id)
    else:
        parser.error("pass --demo or --plan-id")
        return 2

    print(json.dumps(evaluation, indent=2) if args.json else render(evaluation))
    return 0 if evaluation["verdict"] in ("trustworthy", "usable-with-caveats") else 1


if __name__ == "__main__":
    raise SystemExit(main())
