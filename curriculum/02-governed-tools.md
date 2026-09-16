# 02 — Governed tools: designing the refusal

Lab: `python3 labs/lab04_governance_boundaries.py`

## The question most tool design skips

Everyone designs the success path. Almost nobody designs the refusal — and
the refusal is what the agent will hit on the day that matters.

A tool that raises a stack trace teaches the agent nothing, so it retries
the same call. A tool that returns

```json
{"error": "service_inventory carries ['pii'] column(s); this sandbox permits ['internal','public']",
 "reason": "classification",
 "hint": "Pass `select` naming only permitted columns, or create a sandbox cleared for that class."}
```

tells the agent what happened, gives it a machine-readable category to
branch on, and names a next action. The agent corrects itself in one turn.

That is the whole design principle: **a refusal is a return value with a
reason and a workable next step.**

## Four properties of a governed tool

**1. Checked before the side effect.** The sandbox validates state, scope
and budget *before* touching a backend. A budget checked afterwards is an
invoice, not a budget.

**2. Charged for what happened, not what was asked.** `limit=500` returning
12 rows charges 12. A failed query charges nothing. Otherwise agents learn
to pad limits, and your ledger stops meaning anything.

**3. Ordered so the message is true.** Enforcement runs state → scope →
budget. A destroyed sandbox reports "destroyed", not "over budget". The
first true refusal wins, so the agent debugs the real problem.

**4. Escape hatches that are narrower, not wider.** A PII column blocks the
whole product. The hatch is `select` with permitted columns — *narrower*
access. There is no hatch that widens access, because an agent that can
widen its own scope has no scope.

## The one refusal an agent must not "solve"

`reason: "classification"` means narrow the read. It does **not** mean
create a sandbox with PII clearance. An agent that escalates its own
privileges in response to being denied has inverted the control.

This is why `no-self-escalation-of-sandbox-scope` is a declared guardrail
in the ethos manifest and a rule in the agent's SKILL.md — and why the
smoke suite tests for it with a `not_contains` case. Stating it in prose
was not judged sufficient.

## Honesty about the boundary

The sandbox is a policy, scope and spend envelope with an audit ledger. It
is **not** kernel isolation — no VM, no container boundary, no seccomp.

Calling a policy object a "sandbox" and letting people assume gVisor is how
untrusted code ends up with a shell. The word is load-bearing in two
different senses and only one of them is true here. Every document in this
repo says so, and `tests/test_bootstrap.py` asserts that no scaffolded
document claims otherwise.

## Exercise

Run `lab04`. For each of the six boundaries, write down: what an agent
*could* conclude from that message, and what you would want it to conclude.
Where those differ, the message needs work.

Then pick a tool in your own system and write its refusal messages first.
The success path usually gets easier once you have.
