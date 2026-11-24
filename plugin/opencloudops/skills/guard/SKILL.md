---
name: guard
description: Run only the OpenCloudOps security baseline against a resource graph -- no code emitted. Fastest way to check whether a graph would pass.
argument-hint: <graph> [--env dev|staging|prod]
disable-model-invocation: true
metadata:
  plugin: opencloudops
---

Run via Bash:

```
occ guard $ARGUMENTS
```

Report every BLOCK finding first, then WARN findings, each with the plain-
language reason it matters (not just the rule id) and the suggested
remediation the tool prints. State the overall risk tier. A non-zero exit
here means a real blocking finding, not a tool failure -- read the table
before concluding anything is broken.
