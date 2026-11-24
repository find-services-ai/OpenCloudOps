# Service Registry — TEMPLATE
#
# Single source of truth for service → image → runtime mapping.
# Agents consult this file before any release, promotion, or triage.
# Fill this in for YOUR infrastructure.

---

## §1 — Service Inventory

<!-- Copy this row template for each service you manage -->

| Service Key | Source Repo | Image Repo | Compute Type | Dependencies | Health Endpoint |
|---|---|---|---|---|---|
| `my-api` | `my-org/my-api` | `my-api` | ECS Fargate / K8s Deployment / Lambda | RDS, Redis | `/health` |
| `my-worker` | `my-org/my-worker` | `my-worker` | ECS Fargate / K8s Deployment | Kafka, RDS | `/health` |
| `my-frontend` | `my-org/my-frontend` | `my-frontend` | ECS Fargate / CloudFront | API Gateway | ALB health check |

---

## §2 — Service Groups

When a repo publishes a new image, ALL linked services must be updated together.

| Image Repo | Service Group | Release Strategy |
|---|---|---|
| `my-shared-image` | `service-a` + `service-b` | **Atomic** — all updated together |
| `my-independent` | `service-c` | **Independent** — updated alone |

---

## §3 — Health Contracts

Each service declares how the self-healing loop validates it post-deploy.

| Service Key | Healthy Signal | Degraded Signal | Unhealthy Signal |
|---|---|---|---|
| `my-api` | HTTP 200 + `{"status":"UP"}` | 200 but sub-components DOWN | No response / 5xx / crash-loop |
| `my-worker` | Consumer active, lag < 1000 | Lag 1000-10000 | Lag > 10000 or OOM restart |
| `my-frontend` | HTTP 200 on `/` | Slow response (> 5s) | 5xx / no response |

---

## §4 — Image Registry

All images: `{registry_uri}/{repo}:{tag}`

Tag format: *(define your convention)*
- Example: `{semver}-{build_id}` (e.g., `1.2.3-build.456`)
- Example: `{branch}-{sha}` (e.g., `main-abc1234`)

---

## §5 — Log Group Reference

<!-- Map each service to its log group pattern -->

```
# Pattern: {log_group_prefix}/{service}/{env_prefix}
# Examples:
#   ecs/my-app/my-api/d1
#   /aws/lambda/my-app-d1-processor
#   /aws/codebuild/my-repo-dev
```

---

## §6 — Runbook Links

| Service Key | Runbook | Escalation |
|---|---|---|
| `my-api` | [link] | #team-platform Slack |
| `my-worker` | [link] | PagerDuty: my-worker |
