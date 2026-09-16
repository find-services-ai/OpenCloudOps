"""Render a PlatformConfig into a target repository.

Three modes, matching the referenced bootstrap: `plan()` returns
{path: content} without touching disk, `conflicts()` names what already
exists, and `write()` commits. Nothing is overwritten unless the caller
asks, because a bootstrap that silently replaces a repo's CLAUDE.md is a
bootstrap people run once and then never trust again.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from bootstrap import templates
from bootstrap.config import PlatformConfig
from bootstrap.graph import generate_graph, generate_mesh_manifest


def plan(config: PlatformConfig) -> dict[str, str]:
    """Every file this config would produce, as {relative path: content}."""
    files: dict[str, str] = {
        ".github/config/platform.yml": _yaml(config.to_dict()),
        "agent-graph.yml": _yaml(generate_graph(config)),
        ".github/instructions/aidlc.md": templates.aidlc_instructions(config),
        ".github/instructions/guardrails.md": templates.guardrails_instructions(config),
        ".github/skills/_core/cost-investigation/SKILL.md": templates.cost_investigation_skill(config),
        ".github/skills/_core/incident-triage/SKILL.md": templates.incident_triage_skill(config),
    }

    if config.mesh:
        files[".github/skills/_research/mesh-research/SKILL.md"] = templates.mesh_research_skill(config)
        files["mesh.yaml"] = _yaml(generate_mesh_manifest(config))

    # Agent-specific entrypoint. Every agent gets AGENTS.md as well, so a repo
    # scaffolded for one assistant is still legible to another.
    files["AGENTS.md"] = templates.agents_md(config)
    if config.agent == "claude-code":
        files["CLAUDE.md"] = templates.claude_md(config)

    return files


def conflicts(config: PlatformConfig, target: Path) -> list[str]:
    return sorted(p for p in plan(config) if (target / p).exists())


def write(config: PlatformConfig, target: Path, *, overwrite: bool = False) -> list[str]:
    target = Path(target)
    existing = conflicts(config, target)
    if existing and not overwrite:
        raise FileExistsError(
            f"{len(existing)} file(s) already exist and would be replaced: {existing}. "
            "Re-run with overwrite=True (CLI: --overwrite) if that is what you want."
        )

    written: list[str] = []
    for rel, content in plan(config).items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(rel)
    return sorted(written)


def _yaml(data: dict) -> str:
    return yaml.safe_dump(data, sort_keys=False, default_flow_style=False, width=100)
