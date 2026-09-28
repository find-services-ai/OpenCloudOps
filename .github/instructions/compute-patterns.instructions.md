---
description: "Use when: editing compute service definitions, adding new services, or modifying service orchestration files. Covers the for_each pattern, service independence contract, IAM tiering, and container registry patterns."
applyTo: "**/*-layer.tf,**/*-service*.tf,**/services.tf,**/service-locals.tf"
---

# Compute Service Patterns

## Architecture

All services driven by `var.services` map in tfvars. Each service `.tf` is a **complete, self-describing unit**.

```
var.services (tfvars)              per-service .tf files
  ├─ image, sizing, features       ├─ env_vars, secrets
  ├─ health check, scaling          ├─ Secrets Manager resources
  └─ egress rules                  ├─ IAM attachments
       │                           └─ routing map entry
       ▼
services.tf (for_each → compute module)
service-locals.tf (container definitions, computed locals, routing maps)
```

## Adding a New Service (3 touches)

### 1. tfvars — `services` entry

```hcl
new-service = {
  repository    = "{registry_uri}/{repo}"
  tag           = "1.0.0"
  # cpu, memory, desired_count, health_check_path, etc.
}
```

### 2. Service file — `new-service.tf`

Own ALL dependencies: env_vars, secrets, Secrets Manager resources, IAM attachments.

```hcl
locals {
  new_service_env_vars = concat(
    [{ name = "APP_CONFIG", value = "..." }],
    local.common_environment_variables,    # opt-in to shared vars
  )
  new_service_secrets = [
    { name = "DB_PASSWORD", valueFrom = "..." },
  ]
}
resource "aws_iam_role_policy_attachment" "new_service_X" { ... }
```

### 3. Routing map — 2 lines in `service-locals.tf`

```hcl
service_env_vars = { ..., "new-service" = local.new_service_env_vars }
service_secrets  = { ..., "new-service" = local.new_service_secrets }
```

## What's Automatic (zero-touch)

Compute resource, log group, registry IAM, secrets IAM, base networking.

## What's Opt-in (per-service file)

Database access, observability agent, streaming IAM, custom IAM policies, storage access.

## IAM Tiering

| Tier | Scope | Managed In |
|------|-------|------------|
| Universal | Secrets Manager read, log write, registry pull | `iam.tf` (for_each) |
| Service-specific | Database, streaming, storage, custom | `{service-name}.tf` |
| Cross-cutting | VPC access, KMS decrypt | `iam.tf` (conditional) |
