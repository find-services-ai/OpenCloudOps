"""The graph's nodes, as renderable per-agent files.

Ported from ``junction/agent_specs.py``. There, six hand-authored content
dicts supplied each agent's responsibilities/never lists, separate from
``agent-graph.yml``'s own node fields -- two sources of truth an editor had
to remember to keep in sync.

OpenCloudOps has exactly one graph file, ``agent-graph.yaml``, and it already
carries a `description` written for a human to read. This module derives
every per-agent file from that one document -- role, zone, tools, risk gate,
and description all come from the loaded ``AgentGraphSpec``, never from a
second hand-maintained dict. Edit the graph; every adapter's output for that
node changes with it.
"""

from __future__ import annotations

from dataclasses import dataclass

from opencloudops.agentgraph import AgentGraphSpec

#: Graph tool vocabulary that implies read-only. A node is read-only when
#: none of its declared tools can change a file or emit code -- the same
#: test ``graph_invariants.py`` uses to enforce it structurally.
_WRITE_CAPABLE_TOOLS = {"edit", "emit-iac"}

#: Graph MCP tool names -> the MCP server name a target repo's .vscode/mcp.json
#: (or equivalent) would need to define. OpenCloudOps ships no MCP server
#: itself; this is metadata for a target repo's own wiring, same role it
#: played in the source framework.
MCP_TOOL_TO_SERVER = {
    "mcp:cloud-logs": "cloud-logs",
    "mcp:observability": "observability",
}

#: The rule every adapter must repeat verbatim, everywhere, because it is the
#: one thing that must never drift between five different renderings of the
#: same graph.
NEVER_CLAIM_APPLIED = (
    "Never say anything was applied, deployed, or is now running -- occ has "
    "no apply path and holds no cloud credentials."
)


@dataclass
class AgentFileSpec:
    """One graph node, ready to render in any tool's native format."""

    name: str
    role: str
    description: str
    zone: list[str]
    tools: list[str]
    model: str
    risk_gate: str

    @property
    def read_only(self) -> bool:
        return not any(t in _WRITE_CAPABLE_TOOLS for t in self.tools)

    @property
    def mcp_servers(self) -> list[str]:
        return sorted({MCP_TOOL_TO_SERVER[t] for t in self.tools if t in MCP_TOOL_TO_SERVER})

    def body(self, all_specs: list["AgentFileSpec"]) -> str:
        """The operating instructions shared by every tool's rendering."""
        others = ", ".join(s.name for s in all_specs if s.name != self.name)
        lines = [
            f"# {self.role}",
            "",
            self.description.strip(),
            "",
            f"**Zone** (this agent's exclusive concerns): {', '.join(self.zone)}",
            f"**Risk gate**: {self.risk_gate}"
            + (
                " -- may act without a human approval"
                if self.risk_gate == "none"
                else " -- needs a recorded approval before it runs"
            ),
            "",
        ]
        if self.read_only:
            lines += [
                "**Read-only.** This agent never edits a file, never runs `occ iac --out`, "
                "and never touches a cloud account.",
                "",
            ]
        if self.mcp_servers:
            lines += [f"**MCP servers**: {', '.join(self.mcp_servers)}", ""]
        lines += [
            NEVER_CLAIM_APPLIED,
            "",
            f"Other agents in this graph: {others}. Route work you cannot do "
            "yourself back to the orchestrator rather than acting outside your zone.",
        ]
        return "\n".join(lines)


def build_agent_specs(spec: AgentGraphSpec) -> list[AgentFileSpec]:
    """One :class:`AgentFileSpec` per node, in the graph's own node order."""
    return [
        AgentFileSpec(
            name=name,
            role=node.role,
            description=node.description,
            zone=list(node.zone),
            tools=list(node.tools),
            model=spec.models.get(node.model, node.model),
            risk_gate=node.risk_gate,
        )
        for name, node in spec.nodes.items()
    ]
