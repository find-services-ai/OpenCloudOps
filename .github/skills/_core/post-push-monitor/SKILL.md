---
name: post-push-monitor
description: "Use when: immediately after any git push. Autonomous loop that monitors CI/CD, triages failures, applies fixes (up to 3 attempts), and posts to issue tracker only on confirmed success."
---

# Post-Push Monitor — CI/CD Closed Loop

After every `git push`, this skill runs an autonomous monitoring loop that watches the CI/CD pipeline, triages any failures, and either auto-fixes or escalates.

## §1 — Handoff Contract

The IaC Env Manager invokes this skill with:

```
ENV: {env}
BRANCH: {branch}
SHA: {short_sha}
TICKET_IDS: {tickets}
PROFILE: {cli_profile}
CI_PROJECT: {project_name}
LOG_GROUP: {log_group}
CHANGED_SERVICES: {services}
HAS_SERVICE_CHANGES: {bool}
```

## §2 — The Loop

```
1. Wait CI_LEAD_TIME (from deployment-policies.md)
2. Poll CI build status:
   └─ get_build_log(project_name) OR check CI API
3. If BUILDING → wait POLL_INTERVAL, go to 2
4. If SUCCEEDED → exit with SUCCESS
5. If FAILED:
   a. Extract errors: extract_errors(log_group)
   b. Classify error (iac-triage skill)
   c. If fixable (HIGH confidence):
      - Apply fix
      - Commit + push
      - Increment attempt counter
      - If attempts < MAX_REMEDIATION_ATTEMPTS → go to 1
      - Else → ESCALATE
   d. If not fixable → ESCALATE
```

## §3 — Error Classification

| Category | Auto-Fixable | Action |
|----------|-------------|--------|
| Missing resource (data source) | Sometimes | Gate with `count = var.enable_X ? 1 : 0` |
| IAM permission denied | Sometimes | Add missing policy |
| State drift (`already exists`) | No | Escalate (manual import needed) |
| Provider error | No | Escalate |
| Syntax error | Yes | Fix HCL syntax |
| Module output mismatch | Sometimes | Wrap with `try()` |

## §4 — Post-Success Actions

Only after CI SUCCESS + health gate pass:
1. Post deployment summary to issue tracker
2. Record deployment in `memories/deployment-learnings.md`
3. Report final status to user

## §5 — Escalation Format

When escalating, provide:
```
## CI/CD Failure — Escalation

**Environment**: {env}
**Branch**: {branch}
**Build**: {build_id}
**Attempts**: {n}/{max}

### Error
{classified error with full context}

### What Was Tried
{list of auto-fix attempts and results}

### Recommended Manual Action
{specific steps for human to resolve}
```
