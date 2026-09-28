---
name: iac
description: Emit infrastructure-as-code from a resource graph in Terraform, CloudFormation, Pulumi (Python), AWS CDK (TypeScript), or Kubernetes. Writes files only if asked to.
argument-hint: <graph> [--env dev] [--language terraform] [--out DIR]
disable-model-invocation: true
metadata:
  plugin: opencloudops
---

Run via Bash:

```
occ iac $ARGUMENTS
```

Without `--out`, this only lists what would be emitted (a dry run) -- that's
usually what to do first. If the user wants files written to disk, re-run
with `--out <dir> --approve iac-builder` (the MEDIUM gate on code generation
is real; `--approve` is how a human satisfies it, not a formality to skip).

After it runs:

- Summarize what was emitted, per language.
- Carry over any guard findings the underlying plan produced -- `occ iac`
  runs the same guard as `occ plan` before it emits anything.
- If the language isn't one of `terraform`, `cloudformation`, `pulumi-python`,
  `cdk-typescript`, `kubernetes`, say so; don't guess at a name.
- Never claim the emitted code was deployed. It's on disk (or shown), nothing
  more.
