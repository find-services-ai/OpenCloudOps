---
name: self-healing-deploy
description: "Use when: executing a deployment through the full self-healing lifecycle — config update → push → CI → health gate → monitoring → auto-rollback/fix → learning."
argument-hint: "SERVICE_KEY, NEW_TAG, ENV, MULTI_SERVICE (true/false)"
---

# Self-Healing Deploy — Continuous Monitoring Loop

Executes a single service deployment through a self-healing loop that does not terminate until the service is **confirmed healthy** or the agent has **exhausted remediation** and escalated to a human.

## §1 — Entry Contract

**Required inputs:**
- `SERVICE_KEY` — tfvars key
- `NEW_TAG` — target image/version tag
- `ENV` — target environment
- `MULTI_SERVICE` — part of a group deploy?
- `TICKET_IDS` — for traceability

**Pre-conditions (caller ensures):**
- Correct git branch checked out
- No conflicting dirty state
- Service registry loaded
- Past deployment learnings consulted

## §2 — State Machine

```
PHASE 0: PRE-DEPLOY CHECK
  • Known-bad tag check (learnings)
  • Dependency order check (registry service groups)
  • Current service health baseline
       │
       ▼
PHASE 1: DEPLOY
  • Update tfvars tag
  • Commit + push
  • Invoke post-push-monitor
       │
  ┌────┴────┐
  CI OK   CI FAIL → post-push-monitor handles (up to 3 attempts)
  │
  ▼
PHASE 2: SETTLE
  Wait SETTLE_TIME (from deployment-policies.md)
  ECS/K8s task rotation + connection drain
       │
       ▼
PHASE 3: HEALTH GATE
  Query: container health, app health, error rate, deploy marker
  Classify: HEALTHY | DEGRADED | UNHEALTHY
       │
  ┌────┼────────┐
  │    │        │
  ✅   ⚠️       ❌
  │    │        │
  │  EXTENDED   PHASE 4
  │  WINDOW     │
  │    │        ▼
  │    │     DIAGNOSE
  │    │     • Error pattern match
  │    │     • Root cause classification
  │    │     • Confidence level
  │    │        │
  │    │   ┌────┴────┐
  │    │   HIGH    LOW/UNKNOWN
  │    │   │         │
  │    │   FIX       ESCALATE
  │    │   │         (human)
  │    │   └─→ PHASE 1 (retry, max 3)
  │    │
  │    └─→ Re-assess → HEALTHY or ESCALATE
  │
  ▼
PHASE 5: CONFIRM
  • Post success to issue tracker
  • Record learnings
  • Update agent memory
```

## §3 — Remediation Actions

| Diagnosis | Confidence | Auto-Fix |
|-----------|-----------|----------|
| OOM Kill | HIGH | Bump memory (up to `MAX_MEMORY_BUMP_MB`) |
| Health endpoint timeout | MEDIUM | Wait + retry once |
| Config error (known pattern) | HIGH | Fix config, re-push |
| Unknown crash | LOW | Escalate immediately |
| Auth failure | LOW | Escalate (credential issue) |
| Image pull failure | BLOCKED | Escalate (never retry same tag) |

## §4 — Learning

After every deployment (success or failure), record:
- Tag, env, outcome
- Error patterns encountered
- Fix applied and whether it worked
- Time to resolution

Store in `memories/deployment-learnings.md` for future pre-flight checks.
