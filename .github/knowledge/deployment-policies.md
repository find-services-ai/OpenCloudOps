# Deployment Policies — Governance & Risk Guardrails
#
# SINGLE SOURCE OF TRUTH for deployment governance.
# Agent/skill files reference this — they MUST NOT hardcode policy values.
#
# Policy = WHAT is allowed (this file)
# Procedure = HOW to do it (skill files)
# Knowledge = WHAT exists (registry/knowledge files)

---

## §1 — Environment Risk Classification

| Environment | Risk Tier | Autonomous Actions Allowed | HITL Required |
|-------------|-----------|---------------------------|---------------|
| *(lowest)* | LOW | Deploy, auto-rollback, resource bump, retry | Never (except: first-ever service deploy) |
| *(middle)* | MEDIUM | Deploy (after lower env success), auto-rollback | Promotion approval, new service onboarding |
| *(highest)* | HIGH | NOTHING autonomous | ALL changes — deploy, rollback, config change |

### Escalation Override
If any environment experiences **3 consecutive failed deployments** for the same service within 24h → escalate to HITL regardless of risk tier.

---

## §2 — Timing Policies

| Parameter | LOW Risk | MEDIUM Risk | HIGH Risk | Purpose |
|-----------|----------|-------------|-----------|---------|
| `SETTLE_TIME` | 120s | 180s | 300s | Wait for rolling deploy + connection drain |
| `STABILITY_WINDOW` | 300s | 600s | 900s | Soak time to catch delayed failures |
| `POLL_INTERVAL` | 60s | 60s | 60s | API rate limit safety margin |
| `EXTENDED_WINDOW` | 300s | 600s | N/A (escalate) | Second-chance monitoring for degraded |
| `CI_LEAD_TIME` | 90s | 90s | 120s | CI/CD initialization lag |
| `FIX_LEAD_TIME` | 120s | 120s | N/A | Wait after fix push before re-polling |

### Timing Risks
- **Too short `STABILITY_WINDOW`** → declares success before delayed failures (memory leaks, GC pressure)
- **Too long `STABILITY_WINDOW`** → blocks pipeline, delays feedback
- **Mitigation**: Per-service override in `service-registry.md` health contracts

---

## §3 — Remediation Policies

| Parameter | Value | Risk if Wrong |
|-----------|-------|---------------|
| `MAX_REMEDIATION_ATTEMPTS` | 3 | Too high → thrashes 30+ min. Too low → escalates on transient issues |
| `MAX_MEMORY_BUMP_MB` | 1024 | Beyond = code bug, not sizing issue |
| `MAX_CPU_BUMP_UNITS` | 512 | Same as above |
| `KNOWN_BAD_TAG_BLOCK` | HARD | Never auto-deploy a previously rolled-back tag |
| `ROLLBACK_AGE_LIMIT_DAYS` | 7 | Only auto-rollback to tags deployed within 7 days |
| `SAME_ERROR_RETRY` | NEVER | Same error class after fix → escalate immediately |

### Confidence Matrix

| Confidence | Action | Applies To |
|-----------|--------|------------|
| HIGH | Fix and re-enter loop | OOM (bump memory), known config typo |
| MEDIUM | Fix once, then escalate | Health endpoint 503 (wait + retry), SG rule missing |
| LOW | Escalate immediately | Unknown errors, data corruption, multi-service cascade |

### What the Agent MUST NOT Auto-Fix
- Database connection failures (could be failover — wait, don't retry)
- Auth credential failures (rotation needed — HITL)
- Cluster-level capacity issues (not fixable per-service)
- Image pull failures (never retry same tag)
- Multi-service cascading failures (root cause may not be in the failing service)

---

## §4 — Promotion Policies

| Rule | Policy |
|------|--------|
| Promotion order | Must follow environment chain order (no skipping) |
| Pre-promote check | Source env must have successful deploy within 24h |
| Env-specific values | NEVER copied between envs (account IDs, endpoints, secrets) |
| Changelog | Required for all promotions |
| Issue traceability | Required for all commits |
| Env-leak scan | Required after every merge/edit |

---

## §5 — Rollback Policies

| Scenario | Policy |
|----------|--------|
| Auto-rollback (LOW risk) | Allowed — revert to last known-good tag |
| Auto-rollback (MEDIUM risk) | Allowed — but notify team channel |
| Auto-rollback (HIGH risk) | HITL required — present rollback plan, wait for approval |
| Rollback to tag > 7 days old | HITL required regardless of env |
| Rollback of infra (non-image) | HITL required — `terraform plan` first, review diff |
