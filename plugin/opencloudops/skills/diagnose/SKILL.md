---
name: diagnose
description: Classify a production error signal against OpenCloudOps's failure-pattern library and, optionally, get a remediation proposal with its risk tier. Never applies anything.
argument-hint: <service> --signal "<error text>" [--env prod] [--remediate]
disable-model-invocation: true
metadata:
  plugin: opencloudops
---

Run via Bash:

```
occ diagnose $ARGUMENTS
```

If the user didn't say `--remediate` but clearly wants to know what to do
about it, re-run with `--remediate` added.

Report honestly:

- If it matched a known pattern: name it, the confidence level, and the
  matched evidence strings. State the root cause and recommended fix in
  plain language.
- If it did **not** match anything ("unrecognised" / no pattern id): say
  that plainly. Do not invent a plausible-sounding diagnosis to fill the
  gap -- an honest "I don't know, here's what to check" is the correct
  output the tool is designed to produce.
- If a proposal came back: state its risk tier and whether it is autonomous
  or needs a human, and *why* (production is excluded from autonomous
  remediation unconditionally in this framework, regardless of confidence
  or the finding's own risk tier -- that's deliberate, not a bug to explain
  away).
- Never say a fix was applied. A proposal is not an action taken.
