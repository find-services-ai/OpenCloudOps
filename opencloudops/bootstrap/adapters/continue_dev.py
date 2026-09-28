"""Adapter for Continue.dev.

Ported from ``bootstrap/agent_adapters/continue_dev.py`` and corrected
against ``junction/agent_adapters/continue_dev.py`` to render one rule file
per graph node plus a `config.json` whose slash commands are generated from
the same node list, rather than a fixed hand-written set.
"""

from __future__ import annotations

import json

from opencloudops.agentgraph import AgentGraphSpec
from opencloudops.bootstrap.adapters._base import AgentAdapter
from opencloudops.bootstrap.adapters._shared import env_chain
from opencloudops.bootstrap.agent_specs import NEVER_CLAIM_APPLIED, build_agent_specs
from opencloudops.spec import PlatformContract


class ContinueDevAdapter(AgentAdapter):
    name = "continue"

    def generate_files(self, platform: PlatformContract, graph: AgentGraphSpec | None = None) -> dict[str, str]:
        spec = graph or AgentGraphSpec.load("agent-graph.yaml")
        specs = build_agent_specs(spec)
        files: dict[str, str] = {".continue/config.json": _config_json(platform, specs)}
        for agent_spec in specs:
            files[f".continue/rules/{agent_spec.name}.md"] = agent_spec.body(specs)
        return files


def _config_json(p: PlatformContract, specs) -> str:
    chain = env_chain(p)
    config = {
        "models": [],
        "slashCommands": [
            {
                "name": s.name,
                "description": f"{s.role} ({s.risk_gate})",
                "prompt": (
                    f"You are the {s.name} agent for {p.org}/{p.business_unit}. {s.description.strip()} "
                    f"{NEVER_CLAIM_APPLIED} User request: {{{{{{ input }}}}}}"
                ),
            }
            for s in specs
        ],
        "contextProviders": [{"name": "file", "params": {}}],
        "customInstructions": (
            f"Infrastructure for {p.org}/{p.business_unit} is managed through OpenCloudOps. "
            f"{NEVER_CLAIM_APPLIED} Promotion order: {chain}."
        ),
    }
    return json.dumps(config, indent=2)
