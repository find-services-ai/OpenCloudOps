---
name: use-opencloudops
description: Use when a request would otherwise mean hand-writing Terraform, CloudFormation, Pulumi, CDK, or Kubernetes YAML for a cloud service, designing SLOs/alerts/a runbook, or triaging a production error signal. Grounds infrastructure in an approved pattern catalog and guards it against an enforced security baseline instead of improvising -- and never claims to have applied or deployed anything.
paths:
  - "**/*.graph.yaml"
  - "**/agent-graph.yaml"
  - "**/platform.yaml"
  - "**/*.tf"
metadata:
  plugin: opencloudops
---

## Is `occ` available?

!`command -v occ >/dev/null 2>&1 && occ version 2>/dev/null | head -1 || echo "occ: not found on PATH"`

If it printed "not found", stop and run `/opencloudops:setup` first (or tell the
user to `pip install -e ".[all]"` from an OpenCloudOps checkout), then continue.
Do not fall back to writing Terraform/CloudFormation/Pulumi/CDK/Kubernetes by
hand just because `occ` isn't installed yet -- the whole point of this skill is
that hand-written infrastructure skips the checks below.

## What OpenCloudOps is for

Three things this framework exists specifically to prevent, and how:

1. **Inventing infrastructure nobody reviewed.** An agent may only combine
   nodes from an *approved pattern catalog* (`occ list patterns`). If a
   request needs a node type or provider feature with no approved pattern,
   `occ` refuses and names the gap -- that refusal is correct behavior, not a
   bug to route around. Report the gap; don't hand-write the equivalent HCL
   to "just get it done."
2. **Shipping IaC that violates the security baseline.** Every plan runs
   through an enforced baseline (encryption, private networking, backups,
   tagging, no wildcard IAM, no `0.0.0.0/0` in prod, ...) before anything is
   emitted. A small number of findings have exactly one correct fix and
   `occ` repairs them automatically (see the repair note in `occ plan`'s
   output); everything else is a genuine decision -- surface it, don't
   silently pick an answer.
3. **Claiming a change happened.** `occ` has no apply path and holds no
   cloud credentials. It only ever produces a plan, generated code, or a
   change record. **Never tell the user something was deployed, applied, or
   is now running** -- say what was generated and that a human runs it.

## The workflow

For "design/provision/stand up infrastructure for X":

```
occ plan <graph> --env <env>              # ground + guard, no files written
occ iac <graph> --env <env> --language terraform   # emit (add --out DIR --approve iac-builder to write)
occ slo <graph> --env <env> --runbook runbook.md   # SLOs, burn-rate alerts, the on-call doc
```

No `*.graph.yaml` exists yet for the service in question? Don't write Terraform
freehand instead -- either author a small resource graph yourself (provider-
neutral node types: `compute.container_service`, `store.relational`,
`queue.standard`, ...; see `graphs/reference-web-service.graph.yaml` in the
OpenCloudOps repo for the shape), or point discovery at an existing app
directory (Dockerfile + requirements/package.json) with
`python scripts/generate_ideas_examples.py`-style discovery -- see
`opencloudops/integrations/ideas.py`.

For "triage this error" / "what's wrong with `<signal>`":

```
occ diagnose <service> --signal "<the actual error text>" --env <env> --remediate
```

Report the classification honestly. If it printed "no known failure shape
matched," say that plainly -- don't guess a plausible-sounding cause. If a
proposal comes back, say whether it's autonomous or needs a human, and why
(production remediation is *never* autonomous, by design, regardless of the
finding's confidence).

## Manual commands

`/opencloudops:plan`, `/opencloudops:iac`, `/opencloudops:guard`,
`/opencloudops:slo`, `/opencloudops:diagnose`, `/opencloudops:catalog`,
`/opencloudops:setup` -- each wraps one `occ` subcommand. Prefer invoking
`occ` directly via Bash when you already know the right flags; the manual
skills exist for a person typing the command themselves.
