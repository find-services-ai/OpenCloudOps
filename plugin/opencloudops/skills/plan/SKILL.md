---
name: plan
description: Ground and guard a resource graph with OpenCloudOps -- shows the resolved plan and every security finding. Emits and applies nothing.
argument-hint: <graph> [--env dev|staging|prod] [--ticket ID]
disable-model-invocation: true
metadata:
  plugin: opencloudops
---

Run, via the Bash tool (not shell injection -- a blocking finding exits
non-zero on purpose and you need to see the output, not have the skill abort):

```
occ plan $ARGUMENTS
```

If no arguments were given, first run `occ list graphs` and ask which one.

Then, regardless of the exit code:

- State the outcome (PASS / blocked) and the risk tier.
- List every finding (BLOCK and WARN) in your own words -- what it means and
  why it matters, not just the rule id.
- If anything under "repaired automatically" appears, name what changed and
  why that fix has exactly one correct answer (e.g. a production database
  going single-AZ has one fix: Multi-AZ).
- If it says "no pattern for ... on ...", that is OpenCloudOps correctly
  refusing to invent infrastructure. Say so, and suggest adding a pattern to
  `patterns/*.yaml` rather than working around it.
- Never say anything was deployed or applied -- this only grounds and guards.
