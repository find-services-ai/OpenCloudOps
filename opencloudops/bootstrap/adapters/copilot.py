"""Adapter for GitHub Copilot.

Ported from ``bootstrap/agent_adapters/copilot.py`` and corrected against
``junction/agent_adapters/copilot.py`` to render one ``.agent.md`` per graph
node, each declaring the tools it may use and, for a read-only node, no
write tool at all -- so the read/write boundary is a tool-list difference
Copilot itself enforces, not just a line of prose asking nicely.
"""

from __future__ import annotations

from opencloudops.agentgraph import AgentGraphSpec
from opencloudops.bootstrap.adapters._base import AgentAdapter
from opencloudops.bootstrap.agent_specs import AgentFileSpec, build_agent_specs
from opencloudops.spec import PlatformContract

_TOOL_MAP = {
    "read": ["read_file"],
    "search": ["search_files"],
    "grep": ["search_files"],
    "edit": ["read_file", "write_file"],
    "emit-iac": ["write_file", "run_in_terminal"],
    "terminal": ["run_in_terminal"],
    "recall-memory": ["read_file"],
}


class CopilotAdapter(AgentAdapter):
    name = "copilot"

    def generate_files(self, platform: PlatformContract, graph: AgentGraphSpec | None = None) -> dict[str, str]:
        spec = graph or AgentGraphSpec.load("agent-graph.yaml")
        specs = build_agent_specs(spec)
        return {f".github/agents/{s.name}.agent.md": _agent_file(s, specs, platform) for s in specs}


def _copilot_tools(agent_spec: AgentFileSpec) -> list[str]:
    tools: list[str] = []
    for tool in agent_spec.tools:
        mapped = ["fetch_webpage"] if tool.startswith("mcp:") else _TOOL_MAP.get(tool, [])
        for m in mapped:
            if m not in tools:
                tools.append(m)
    return tools


def _agent_file(agent_spec: AgentFileSpec, all_specs: list[AgentFileSpec], p: PlatformContract) -> str:
    handoffs = ", ".join(s.name for s in all_specs if s.name != agent_spec.name)
    return f"""---
name: {agent_spec.name}
description: "{agent_spec.role} -- {p.org}/{p.business_unit}. {agent_spec.description.strip().splitlines()[0]}"
tools: [{", ".join(_copilot_tools(agent_spec))}]
agents: [{handoffs}]
---

{agent_spec.body(all_specs)}
"""
