# 05 — Evaluating an on-demand research run

Lab: `python3 labs/lab05_evaluate_a_run.py`
Rubric: `evaluation/rubric.yaml` · Scorer: `evaluation/evaluate.py`

## Grade the run, not the answer

Whether a conclusion is *correct* is a domain question no rubric settles.
Whether a run was **scoped, cited, reproducible, and honest about its
gaps** is decidable from artefacts the run left behind.

That distinction is the point. A run that fails those four is untrustworthy
even when its conclusion happens to be right — because you have no way to
know it is right, and no way to find out later.

## The eight criteria

| Criterion | W | Catches |
|---|---|---|
| `evidence_completeness` | 5 | A finding with no ledger record — **and** a successful step with no finding |
| `no_restricted_in_evidence` | 5 | A PII column written into a stored sample |
| `evidence_reproducible` | 4 | A digest that no longer reproduces |
| `gaps_declared` | 4 | An unreached plane the report does not mention |
| `plane_coverage` | 3 | A cross-domain question answered from one plane |
| `scope_minimality` | 3 | A sandbox that allowed four planes and used two |
| `budget_discipline` | 2 | Steps lost to the row budget |
| `sandbox_closed` | 2 | Standing authority nobody is watching |

Two are **disqualifying**: an uncited finding and a leaked restricted
column cap the verdict at `untrustworthy` however high the weighted score.
Neither is recoverable by re-running — the PII value is already written.

## Three loopholes this rubric had, and how they closed

Worth reading, because your first rubric will have the same shape.

**`scope_minimality` had a free escape hatch.** A wide sandbox passed
whenever the report listed *any* gap — and the planner always produces one,
so every wide run passed. It now requires that a wide scope was actually
*used*: every allowed plane produced evidence, or the criterion fails.

**`budget_discipline` rewarded starvation.** It checked remaining headroom.
A run whose steps were refused for budget *stops spending*, so it ends with
headroom and passed. It now inspects the failed steps for budget refusals
first. Leftover budget is not discipline if it is leftover because the run
gave up.

**`evidence_completeness` only checked forward.** Every finding resolved to
a record — but findings are *derived from* the ledger, so deleting a record
deletes the finding too and nothing dangles. It now also checks backward:
every step the run reported as successful must be represented. A report
that silently drops a step it claims to have run is exactly the quiet
incompleteness this rubric exists to catch.

The pattern in all three: **a criterion that can be satisfied by accident
measures nothing.** When you write one, ask what a lazy run would do, and
check whether that run passes.

## The auditor spends its own budget

`evidence_reproducible` re-executes each evidence query in a fresh audit
sandbox scoped to the same planes. It does not quietly reuse the subject's
budget — an audit that consumes the thing it is auditing changes it.

## Exercise

1. Run `lab05`. Read why Run B lost each point.
2. Add a ninth criterion to `rubric.yaml` and implement `_your_criterion`
   on `Evaluator`. `test_every_rubric_criterion_has_an_implementation` will
   fail until you do — that is deliberate.
3. Then attack your own criterion: what is the laziest run that passes it?
   If you can construct one in under a minute, the criterion is decoration.
