---
name: my-domain-skill
description: "Use when: {describe the trigger conditions for this skill}"
---

# Skill: {Skill Name}

{One-line description of what this skill does.}

## Prerequisites

- {List what must be true before this skill can run}
- {e.g., "MCP servers running", "Service registry loaded"}

## Triggers

This skill is invoked when:
- {Pattern or keyword that triggers it}
- {e.g., "User mentions consumer lag"}
- {e.g., "Error pattern matches X"}

## Workflow

### Step 1: {Gather Context}

{What data does the skill need? Where does it come from?}

### Step 2: {Analyze / Classify}

{What logic does the skill apply?}

| Classification | Criteria | Action |
|---------------|----------|--------|
| {Case A} | {When...} | {Do...} |
| {Case B} | {When...} | {Do...} |

### Step 3: {Act / Report}

{What does the skill produce? A fix? A report? A recommendation?}

## Output Format

```
## {Skill Name} Report

**Context**: {what was analyzed}
**Finding**: {result}
**Recommendation**: {next steps}
```

## Integration Points

- **Invoked by**: {which agent or skill calls this}
- **Delegates to**: {which agent or skill this calls}
- **MCP tools used**: {which MCP server tools}
