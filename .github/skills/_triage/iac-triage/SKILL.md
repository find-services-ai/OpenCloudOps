---
name: iac-triage
description: "Use when: IaC plan/apply fails in CI/CD, diagnosing pipeline errors, correlating infrastructure failures with observability data, or triaging any build failure."
---

# Skill: IaC Failure Triage

Diagnose Terraform/OpenTofu/Pulumi/CDK plan/apply failures using MCP tools. Classify errors, apply fixes, validate.

## §1 — Acquire Error (MCP-only)

Try in order — stop at first success:

| Source | Tool |
|--------|------|
| Cloud Logs | `extract_errors(log_group, hours_back=6)` |
| CI Build | `get_build_log(project_name)` |
| Custom query | `query_logs(log_group, "filter @message like /Error:|FAILED/")` |
| Service logs | `get_log_events(log_group, filter_pattern="ERROR")` |

If user provides a log file, read it with `read_file` and extract error lines.

## §2 — Error Classification

| Category | Pattern | Common Cause |
|----------|---------|-------------|
| Missing resource | `couldn't find resource` | Artifact not in target env |
| ARN format mismatch | `AccessDenied` + valid-looking ARN | Wrong identifier type |
| Data source failure | `data.aws_*: couldn't find` | SSM param or object missing |
| Module attribute gap | `Unsupported attribute` | Module doesn't expose output |
| Provider deprecation | `Invalid Attribute Combination` | Missing required block |
| IAM auth failure | `AccessDenied`, `not authorized` | Missing policy or wrong ARN |
| Network/connectivity | `timeout`, `connection refused` | SG rule or endpoint missing |
| State drift | `already exists` | Manual change outside IaC |
| Syntax error | `Invalid expression` | HCL/YAML syntax issue |
| Dependency cycle | `Cycle` | Circular resource reference |

## §3 — Root Cause + Fix

For each classified error:
- **Immediate fix** — unblock the pipeline now
- **Proper fix** — long-term maintainability
- **Prevention** — guard to prevent recurrence

Common fix patterns:
- Missing resource → gate with `count = var.enable_X ? 1 : 0`
- SSM param not found → create in target account or gate
- Module output type → wrap with `try(tostring(x), join(",", x))`
- State drift → `terraform import` (HITL)

## §4 — Post-Fix Validation

After fix deployed:
1. `get_build_log` → confirm build succeeds
2. `triage_service(service, env)` → verify no error spike
3. `get_app_health(app)` → confirm response times nominal

## §5 — Output Format

```
## IaC Failure Triage

**Environment**: {env}
**Build**: {build_id}
**Error Category**: {category}

### Error
{exact error message}

### Root Cause
{explanation}

### Fix Applied
{what was changed}

### Prevention
{how to prevent recurrence}
```
