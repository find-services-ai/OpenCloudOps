"""The evaluator has to catch a bad run. A rubric that only ever passes is
decoration, so most of these tests build a deliberately flawed run and
assert the specific criterion that should fail."""

from __future__ import annotations

import pytest
import yaml

from conftest import requires_sandbox_research  # noqa: F401  (import for the marker)
from tests.conftest import requires_sandbox_research as _requires  # noqa: F401

from evaluate import RUBRIC_PATH, Evaluator, load_rubric, render


def test_rubric_is_well_formed():
    rubric = load_rubric()
    ids = [c["id"] for c in rubric["criteria"]]
    assert len(ids) == len(set(ids)), "duplicate criterion id"
    assert all(c["weight"] > 0 and c["description"] and c["rationale"] for c in rubric["criteria"])
    assert set(rubric["disqualifying"]) <= set(ids)
    mins = [v["min"] for v in rubric["verdicts"]]
    assert mins == sorted(mins, reverse=True), "verdicts must be ordered high to low"
    assert mins[-1] == 0.0, "there must be a catch-all verdict"


def test_every_rubric_criterion_has_an_implementation():
    rubric = yaml.safe_load(RUBRIC_PATH.read_text())
    for criterion in rubric["criteria"]:
        assert hasattr(Evaluator, f"_{criterion['id']}"), criterion["id"]


@pytest.fixture
def run_factory(lab_env):
    """Build a research run with controllable sandbox parameters."""
    platform, call = lab_env
    from src.research import ResearchEngine

    def make(*, close=True, **sandbox_kwargs):
        sandbox = call("sandbox_create", **{"purpose": "evaluation test", **sandbox_kwargs})
        plan = ResearchEngine(platform).plan(
            sandbox["id"], "Why is weekly cloud cost rising and which incidents explain it?", max_steps=4
        )
        call("research_run", plan_id=plan.id, rows_per_step=40)
        if close:
            call("sandbox_destroy", sandbox_id=sandbox["id"])
        return plan.id

    return make


@_requires
def test_a_disciplined_run_is_trustworthy(lab_env, run_factory):
    platform, _ = lab_env
    plan_id = run_factory(
        ttl_seconds=900, max_queries=25, max_rows=15_000, allowed_planes=["warehouse", "lake", "bi"]
    )
    result = Evaluator(platform).evaluate(plan_id)
    assert result["verdict"] == "trustworthy", render(result)
    assert result["score"] == 1.0


@_requires
def test_an_open_sandbox_is_caught(lab_env, run_factory):
    platform, _ = lab_env
    plan_id = run_factory(close=False, max_queries=25, max_rows=15_000, allowed_planes=["warehouse", "lake"])
    result = Evaluator(platform).evaluate(plan_id)
    assert _failed(result) >= {"sandbox_closed"}


@_requires
def test_a_starved_budget_is_caught_even_with_headroom_left(lab_env, run_factory):
    """The loophole worth testing: a run can end with spare queries precisely
    because it stopped costing once its steps started failing."""
    platform, _ = lab_env
    plan_id = run_factory(max_queries=3, max_rows=60)
    result = Evaluator(platform).evaluate(plan_id)
    sandbox = platform.sandboxes.status(platform.store.get_plan(plan_id)["sandbox_id"])
    assert sandbox["budget"]["max_queries"] > sandbox["usage"]["queries"], "headroom remains"
    assert "budget_discipline" in _failed(result)


@_requires
def test_unused_breadth_is_caught(lab_env, run_factory):
    """A wide sandbox passes scope_minimality only if the breadth was used."""
    platform, _ = lab_env
    plan_id = run_factory(max_queries=25, max_rows=15_000)  # all four planes, api unreachable
    result = Evaluator(platform).evaluate(plan_id)
    assert "scope_minimality" in _failed(result)


@_requires
def test_evidence_lost_after_a_successful_step_disqualifies_the_run(lab_env, run_factory):
    """The failure a forward-only citation check misses.

    `research_report` derives findings FROM the ledger, so deleting an
    evidence record also deletes the finding that cited it -- no dangling
    citation appears. But the plan still records the step as successful, and
    a report that silently drops a step it claims to have run is exactly the
    kind of quiet incompleteness this rubric exists to catch.
    """
    platform, _ = lab_env
    plan_id = run_factory(max_queries=25, max_rows=15_000, allowed_planes=["warehouse", "lake", "bi"])
    assert Evaluator(platform).evaluate(plan_id)["verdict"] == "trustworthy"

    evidence = platform.store.evidence_for_plan(plan_id)
    platform.store._conn.execute("DELETE FROM evidence WHERE id = ?", (evidence[0]["id"],))
    platform.store._conn.commit()

    after = Evaluator(platform).evaluate(plan_id)
    assert "evidence_completeness" in _failed(after)
    assert after["verdict"] == "untrustworthy", "a disqualifying criterion caps the verdict"
    assert after["score"] > 0.5, "the score stays high -- the disqualification is what caps it"


@_requires
def test_a_run_with_no_evidence_at_all_is_disqualified(lab_env, run_factory):
    platform, _ = lab_env
    plan_id = run_factory(max_queries=25, max_rows=15_000, allowed_planes=["warehouse", "lake"])
    platform.store._conn.execute("DELETE FROM evidence WHERE plan_id = ?", (plan_id,))
    platform.store._conn.commit()

    result = Evaluator(platform).evaluate(plan_id)
    assert result["verdict"] == "untrustworthy"
    assert "evidence_completeness" in result["disqualified_by"]
    assert result["verdict_note"].startswith("Do not act")


@_requires
def test_a_changed_source_breaks_reproducibility(lab_env, run_factory):
    platform, _ = lab_env
    plan_id = run_factory(max_queries=25, max_rows=15_000, allowed_planes=["warehouse"])
    evidence = platform.store.evidence_for_plan(plan_id)
    assert evidence
    tampered = dict(evidence[0])
    tampered["digest"] = "sha256:" + "0" * 64
    platform.store.put_evidence(tampered["id"], plan_id, tampered)

    result = Evaluator(platform).evaluate(plan_id)
    assert "evidence_reproducible" in _failed(result)


@_requires
def test_render_is_readable_and_names_every_criterion(lab_env, run_factory):
    platform, _ = lab_env
    plan_id = run_factory(max_queries=25, max_rows=15_000, allowed_planes=["warehouse", "lake"])
    text = render(Evaluator(platform).evaluate(plan_id))
    for criterion in load_rubric()["criteria"]:
        assert criterion["id"] in text
    assert "Verdict:" in text


def _failed(result) -> set[str]:
    return {c["id"] for c in result["criteria"] if not c["passed"]}
