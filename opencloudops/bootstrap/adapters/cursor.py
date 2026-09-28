"""Adapter for Cursor.

Ported from ``bootstrap/agent_adapters/cursor.py`` and corrected against
``junction/agent_adapters/cursor.py`` to render one ``.mdc`` rule per graph
node instead of two hand-written rules -- the graph is the source of truth.
"""

from __future__ import annotations

from opencloudops.agentgraph import AgentGraphSpec
from opencloudops.bootstrap.adapters._base import AgentAdapter
from opencloudops.bootstrap.adapters._shared import env_chain, env_table_rows
from opencloudops.bootstrap.agent_specs import NEVER_CLAIM_APPLIED, build_agent_specs
from opencloudops.spec import PlatformContract


class CursorAdapter(AgentAdapter):
    name = "cursor"

    def generate_files(self, platform: PlatformContract, graph: AgentGraphSpec | None = None) -> dict[str, str]:
        spec = graph or AgentGraphSpec.load("agent-graph.yaml")
        specs = build_agent_specs(spec)
        files: dict[str, str] = {}
        for agent_spec in specs:
            always = agent_spec.name == spec.entry
            globs = [] if agent_spec.read_only else ["**/*.graph.yaml", "**/*.tf", "agent-graph.yaml"]
            files[f".cursor/rules/{agent_spec.name}.mdc"] = _rule(agent_spec, specs, platform, globs, always)
        return files


def _rule(agent_spec, all_specs, p: PlatformContract, globs: list[str], always: bool) -> str:
    glob_line = ", ".join(f'"{g}"' for g in globs)
    return f"""---
description: {agent_spec.role} -- {p.org}/{p.business_unit}. {agent_spec.description.strip().splitlines()[0]}
globs: [{glob_line}]
alwaysApply: {"true" if always else "false"}
---

{agent_spec.body(all_specs)}

## Environments

| env | short | region | risk |
|---|---|---|---|
{env_table_rows(p)}

Promotion order: `{env_chain(p)}`.
"""
