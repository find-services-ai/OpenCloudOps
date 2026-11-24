---
description: "Use when: editing IaC files (.tf, .tfvars, .yaml, .yml). Enforces naming conventions, module patterns, environment variable rules, and deployment chain awareness."
applyTo: "**/*.tf,**/*.tfvars"
---

# IaC Standards

## Naming

`{application}-{environment}-{service}[-{suffix}]`
- Defined in `platform.yml` naming section
- Computed in locals — never hardcode naming patterns in resources

## File Organization

- One `.tf` per logical component / service
- Per-service locals in same `.tf` file (env vars, secrets, IAM)
- Env-specific values in `tfvars/{env}.tfvars` ONLY — NEVER hardcode ARNs in `.tf`
- Disabled features use `.tfx` extension (rename to `.tf` to activate)
- Cross-env values that differ (endpoints, secrets, broker addresses) belong in tfvars

## Modules

- Pin versions always: `?ref=v{N}` or version constraint
- Check module outputs — some may return lists where you expect strings
- Wrap with `try(tostring(x), join(",", x))` for safety

## Security Groups

- NEVER `0.0.0.0/0` in production egress
- Use `source_security_group_id` within same VPC
- Mark temporary broad rules with `# TODO: Restrict`
- Streaming ports: use IAM auth port only (e.g., 9098 for MSK IAM)

## Secrets Management

- Names: `{prefix}-{purpose}`
- NEVER hardcode credentials
- Use `jsonencode({})` for initial values
- `lifecycle { ignore_changes = [secret_string] }` always

## Tags

Always `tags = var.tags` or `merge(var.tags, { Component = "..." })`.

## Environment Promotion Guardrails

Before merging across environments:
1. Scan for env-leak patterns (`platform.yml` leak_patterns)
2. Verify no hardcoded account IDs, endpoints, or secret ARNs crossed environments
3. Generate changelog entry
4. Enforce issue ticket in commit message
