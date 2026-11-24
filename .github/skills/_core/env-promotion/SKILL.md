---
name: env-promotion
description: "Use when: promoting IaC changes across environments, deploying to a target environment, or synchronizing service versions between environments."
---

# Skill: Environment Promotion

Safely promote infrastructure or application changes from one environment to another following the environment chain defined in `platform.yml`.

## Guardrails

1. Run Boot Protocol before any action
2. NEVER copy env-specific values between environments
3. NEVER auto-approve highest-risk environment
4. ALWAYS generate Changelog entry before push
5. ALWAYS invoke push-gate before push
6. ALWAYS audit for env-leak after merge

## Workflow

### Phase 0: Orient & Isolate

Boot Protocol. For cross-branch promotions, offer worktree:
```
git worktree add "../{repo}-promote-{ticket}-{target}" -b "promote/{ticket}-{src}-to-{tgt}" {target}
```

### Phase 1: Identify Promotion Type

- **Image/version update**: Update tags in `tfvars/{target}.tfvars`
- **Infrastructure change**: Modified `.tf` files (shared code, env-specific tfvars)
- **Full merge**: Merge source branch → target

### Phase 2: Read Source + Target

Read: `tfvars/{source}.tfvars`, `tfvars/{target}.tfvars`, service orchestration files

### Phase 3: Classify Differences

| Category | Action | Examples |
|----------|--------|----------|
| Env-unique | KEEP target | KMS keys, account IDs, endpoints, secret ARNs |
| Promotable | UPDATE to source | Image tags, feature flags, config values |
| Scaling | KEEP target (unless intentional) | desired_count, capacity, instance size |

### Phase 4: Merge + Resolve

```
git checkout {target}
git merge {source} --no-edit
# Resolve conflicts (env-unique values always keep target)
```

### Phase 5: Env-Leak Scan

Scan all IaC files for patterns from `platform.yml` `leak_patterns`. Any match → STOP.

### Phase 6: Plan + Approve

Generate plan. Present to user with risk tier from `deployment-policies.md`.

### Phase 7: Changelog + Push

Update `Changelog.md`. Invoke push-gate. Push.
