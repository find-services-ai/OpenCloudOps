---
name: slo
description: Derive SLOs, error budgets, two-window burn-rate alerts, and a runbook from a resource graph's plan -- the SRE setup, not the infrastructure itself.
argument-hint: <graph> [--env dev] [--runbook FILE]
disable-model-invocation: true
metadata:
  plugin: opencloudops
---

Run via Bash:

```
occ slo $ARGUMENTS
```

Summarize the objectives table (node, objective %, error budget, p95 latency
where set). Explain the two alert tiers plainly: a fast-burn alert (~14.4x
the sustainable error rate over 1h) pages someone now; a slow-burn alert
(~3x over 6h) is a ticket, not a page. If `--runbook` was passed, confirm
where it was written and mention it documents the blast radius (apply
order) and what's explicitly never auto-remediated.
