---
name: drift-detection
description: "Use when: comparing environments for configuration drift, checking if environments are in sync, finding unintentional differences between tfvars or service versions."
---

# Skill: Drift Detection

Compare environment configurations to find unintentional drift. Reads tfvars and reports differences classified by intent.

## Workflow

1. **Select environments** — Source and target (e.g., dev vs staging, staging vs prod)
2. **Read tfvars** — `tfvars/{env}.tfvars` for both
3. **Diff service versions** — Image tags, feature flags
4. **Classify each difference**:

| Classification | Meaning | Action |
|---------------|---------|--------|
| **Intentional** | Env-specific (account IDs, endpoints) | No action |
| **Version lag** | Lower env has newer version | Promote when ready |
| **Config drift** | Same param, different value, not env-specific | Investigate |
| **Missing service** | Service exists in one env but not the other | Intentional? |

5. **Report** — Structured diff with classifications

## Output Format

```
## Drift Report: {source} vs {target}

### Version Lag (promotable)
| Service | {source} | {target} | Delta |
|---------|----------|----------|-------|
| my-api  | 1.5.0    | 1.4.0   | +1 minor |

### Config Drift (investigate)
| Parameter | {source} | {target} | Risk |
|-----------|----------|----------|------|
| desired_count.my-api | 2 | 1 | Low (scaling) |

### Env-Specific (expected)
| Parameter | {source} | {target} |
|-----------|----------|----------|
| account_id | 111... | 222... |
```
