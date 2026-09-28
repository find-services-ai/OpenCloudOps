---
name: sre-log-fetching
description: "Use when: fetching logs on-demand from cloud logging or log aggregation for any service, function, CI/CD build, or infrastructure component."
---

# Skill: SRE On-Demand Log Fetching

Fetch and analyze logs from cloud logging and log aggregation services for any platform component.

## §1 — Source Selection

| Scenario | Source | Tool |
|----------|--------|------|
| Service errors (< 24h) | Cloud Logs | `get_log_events` |
| Service errors (> 24h) | Log Aggregator | `get_service_errors` |
| CI/CD build output | Cloud Logs | `get_build_log` / `extract_errors` |
| Complex aggregation | Cloud Logs Insights | `query_logs` |
| Full-text cross-service | Log Aggregator | `search_logs` |
| Function execution errors | Cloud Logs | `get_log_events` |

## §2 — Fetch Workflow

1. **Resolve environment** → map to prefix using `platform.yml`
2. **Resolve service** → match against service registry
3. **Determine time window** → default 1h active, 24h investigation
4. **Choose source** → use selection matrix
5. **Execute** → return structured results with timestamps
6. **Classify** → apply error pattern library

## §3 — Quick-Fetch Patterns

```
# "Show me errors for {service} in {env} last 2 hours"
→ get_log_events(log_group="{pattern}/{service}/{prefix}", filter_pattern="ERROR|Exception", hours_back=2)

# "What failed in the last CI build for {env}?"
→ get_build_log(project_name="{repo}-{env}")

# "Search for auth failures across all services in {env}"
→ search_logs(index="{index_pattern}", query_string="AuthenticationException OR AccessDenied", hours_back=12)

# "Function execution errors"
→ get_log_events(log_group="/aws/lambda/{fn-name}", filter_pattern="ERROR", hours_back=4)
```

## §4 — Output Format

Always include:
- Timestamp range queried
- Source (which log group/index)
- Hit count
- Top errors (deduplicated, with frequency)
- Raw samples (up to 10)
