---
description: "Use when: promoting changes to the highest-risk environment, editing secrets or credentials in IaC files, or reviewing production readiness. Enforces immutability of production credentials and mandatory human approval."
applyTo: "**/*.tf,**/*.tfvars"
---

# Production Release Guardrails

## Absolute Rules

1. **NEVER auto-approve** changes to the highest-risk environment
2. **NEVER copy** env-specific values (account IDs, endpoints, secrets ARNs) from lower envs
3. **ALWAYS run full plan** and present to user before apply
4. **ALWAYS run env-leak scan** before push
5. **ALWAYS generate changelog** entry with ticket references
6. **ALWAYS verify** successful deploy in lower env within 24h

## Credential Immutability

Production credentials and connection parameters that are managed externally (rotated by security teams, provisioned by partner systems, etc.) are **IMMUTABLE** in IaC:

- Database connection strings managed by DBA team
- API keys rotated by security automation
- Certificate ARNs managed by PKI team
- External system endpoints provided by partners

**Rule**: If you didn't create the credential, you don't modify it. Use `ignore_changes` lifecycle.

## Pre-Push Checklist

- [ ] Full `terraform plan` reviewed
- [ ] No env-leak patterns detected
- [ ] Changelog updated
- [ ] Ticket ID in commit message
- [ ] Lower env deploy successful within 24h
- [ ] No `TODO: Restrict` security group rules going to prod
- [ ] All secrets use `ignore_changes = [secret_string]`
