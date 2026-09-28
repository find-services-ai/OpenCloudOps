"""Adapter for Claude Code.

Ported from ``bootstrap/agent_adapters/claude_code.py`` in the original zip
and then corrected against ``junction/agent_adapters/claude_code.py`` (the
evolved successor project), which renders **one subagent file per graph
node** rather than a single project-context file. This version does the
same: ``CLAUDE.md`` plus one ``.claude/agents/<node>.md`` per node in
``agent-graph.yaml``, so a change to the graph -- a new node, a different
zone, a raised risk gate -- changes what gets scaffolded without touching
this file.
"""

from __future__ import annotations

import json

from opencloudops.agentgraph import AgentGraphSpec
from opencloudops.bootstrap.adapters._base import AgentAdapter
from opencloudops.bootstrap.adapters._shared import env_chain, env_table_rows, occ_command_table
from opencloudops.bootstrap.agent_specs import NEVER_CLAIM_APPLIED, AgentFileSpec, build_agent_specs
from opencloudops.spec import PlatformContract

#: Graph tool vocabulary -> Claude Code tool names.
_TOOL_MAP = {
    "read": ["Read", "Glob"],
    "search": ["Grep", "Glob"],
    "grep": ["Grep"],
    "edit": ["Edit", "Write"],
    "emit-iac": ["Write", "Bash"],
    "terminal": ["Bash"],
    "recall-memory": ["Read", "Grep"],
}


class ClaudeCodeAdapter(AgentAdapter):
    name = "claude-code"

    def generate_files(self, platform: PlatformContract, graph: AgentGraphSpec | None = None) -> dict[str, str]:
        spec = graph or AgentGraphSpec.load("agent-graph.yaml")
        specs = build_agent_specs(spec)
        files: dict[str, str] = {"CLAUDE.md": _claude_md(platform, specs)}
        for agent_spec in specs:
            files[f".claude/agents/{agent_spec.name}.md"] = _subagent_file(agent_spec, specs)
        for cmd_name, content in (
            ("plan", _cmd_plan()),
            ("iac", _cmd_iac()),
            ("guard", _cmd_guard()),
            ("slo", _cmd_slo()),
            ("diagnose", _cmd_diagnose()),
            ("promote", _cmd_promote(platform)),
        ):
            files[f".claude/commands/{cmd_name}.md"] = content
        return files


def _claude_tools(agent_spec: AgentFileSpec) -> list[str]:
    tools: list[str] = []
    for tool in agent_spec.tools:
        mapped = ["Bash"] if tool.startswith("mcp:") else _TOOL_MAP.get(tool, [])
        for m in mapped:
            if m not in tools:
                tools.append(m)
    return tools


def _subagent_file(agent_spec: AgentFileSpec, all_specs: list[AgentFileSpec]) -> str:
    lines = [
        "---",
        f"name: {agent_spec.name}",
        f"description: {json.dumps(agent_spec.role + ' -- ' + agent_spec.description.strip().splitlines()[0], ensure_ascii=False)}",
        f"tools: {', '.join(_claude_tools(agent_spec))}",
        "model: inherit",
    ]
    if agent_spec.read_only:
        lines.append("disallowedTools: Edit, Write, NotebookEdit")
    lines.append("---")
    return "\n".join(lines) + "\n\n" + agent_spec.body(all_specs)


def _claude_md(p: PlatformContract, specs: list[AgentFileSpec]) -> str:
    agent_rows = "\n".join(
        f"| `{s.name}` | {s.role} | `{s.risk_gate}` | `.claude/agents/{s.name}.md` |" for s in specs
    )
    return f"""# {p.org}/{p.business_unit} -- OpenCloudOps context

> Scaffolded by `occ bootstrap --agent claude-code` from `agent-graph.yaml`.
> Regenerate rather than hand-editing; the graph is the source of truth.

This repository's infrastructure is managed through **OpenCloudOps**: an
agentic framework that grounds infrastructure in an approved pattern
catalog, guards it against an enforced security baseline, and emits IaC.
{NEVER_CLAIM_APPLIED}

## Environments

| env | short | region | risk | approvers |
|---|---|---|---|---|
{env_table_rows(p)}

Promotion order: `{env_chain(p)}` -- never skip an environment.

## Agent graph

This repo runs the six-node graph in `agent-graph.yaml`. Delegate each step
to exactly one specialist subagent rather than doing its job in the main
session:

| Agent | Role | Gate | File |
|---|---|---|---|
{agent_rows}

## Commands

| Command | What it does |
|---|---|
{occ_command_table()}

Stubs for the most common ones are in `.claude/commands/`.
"""


def _cmd_plan() -> str:
    return "# /plan\n\nRun `occ plan <graph> --env <env>`. Report pass/blocked, risk tier, and every finding in plain language. " + NEVER_CLAIM_APPLIED + "\n"


def _cmd_iac() -> str:
    return (
        "# /iac\n\nRun `occ iac <graph> --env <env> --language <lang>`. Without `--out` this is a dry "
        "run. Only pass `--out DIR --approve iac-builder` if asked to write files to disk.\n"
    )


def _cmd_guard() -> str:
    return (
        "# /guard\n\nRun `occ guard <graph> --env <env>`. Report BLOCK findings first, then WARN. "
        "A non-zero exit is a real finding, not a tool failure.\n"
    )


def _cmd_slo() -> str:
    return (
        "# /slo\n\nRun `occ slo <graph> --env <env> --runbook FILE`. Summarize objectives, error "
        "budgets, and the two burn-rate alert tiers.\n"
    )


def _cmd_diagnose() -> str:
    return (
        '# /diagnose\n\nRun `occ diagnose <service> --signal "<text>" --env <env> --remediate`. '
        "If nothing matched, say \"unrecognised\" -- never invent a plausible root cause. " + NEVER_CLAIM_APPLIED + "\n"
    )


def _cmd_promote(p: PlatformContract) -> str:
    return (
        f"# /promote\n\nRun `occ promote <graph> --env <target>`. Produces a change record for a "
        f"human to execute. Promotion order: `{env_chain(p)}`.\n"
    )
