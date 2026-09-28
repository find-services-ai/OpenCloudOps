# AI Coding Agent Instructions — IaC Agent Platform
#
# Customize this file for YOUR infrastructure repository.
# Replace all {placeholders} with your actual values.

## Agent Operating Model

Managed by **IaC Env Manager** agent (`.github/agents/iac-env-manager.agent.md`). All operations follow its Boot Protocol (§1): confirm branch → environment → act.

**Critical rules:**
- Never assume which branch you are on — always `git branch --show-current` first
- All operational data via MCP tools — never local scripts
- Post-push: agent enters Closed Loop — monitors build, triages failures, posts issue tracker updates

## Environment Reference

<!-- Fill from platform.yml -->
| Env | Branch | Account | Prefix | tfvars |
|-----|--------|---------|--------|--------|
| dev | `dev` | `111111111111` | `d1` | `tfvars/dev.tfvars` |
| staging | `staging` | `222222222222` | `s1` | `tfvars/staging.tfvars` |
| prod | `prod` | `333333333333` | `p1` | `tfvars/prod.tfvars` |

## Key Conventions

- **Service independence**: Each service `.tf` owns env vars, secrets, IAM
- **Image source of truth**: `var.services` in tfvars
- **Secrets lifecycle**: `ignore_changes = [secret_string]` — IaC never overwrites live values
- **MCP-first**: All issue tracking, log, and observability operations via MCP tools

## Infrastructure Organization

```
services.tf           # for_each over var.services
service-locals.tf     # Container definitions, env vars, computed locals
{service-name}.tf     # Per-service: env vars, secrets, IAM
iam.tf                # Shared IAM policies
locals.tf             # Common locals
variables.tf          # Variable definitions
tfvars/               # Per-environment config
```

## Tool Usage Rules

| Need | Use | NEVER use |
|------|-----|-----------|
| Issue tracking | Issue tracker MCP server | Direct API calls, local scripts |
| Cloud logs/builds | Cloud logs MCP server | CLI log commands |
| Observability | Observability MCP server | Direct API calls |
| File search | `grep_search` agent tool | Shell grep/find |
| Git operations | Terminal | — |

## Variable Management

- `var.services` is single source of truth for service deployments
- `tfvars/{env}.tfvars` for env overrides — NEVER hardcode env-specific values in `.tf` files
