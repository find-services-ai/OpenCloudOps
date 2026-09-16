"""Generate a CloudOps agent graph from a PlatformConfig.

Same generator idea as the referenced IaC/SRE bootstrap: derive the graph
from answers rather than hand-maintaining a YAML nobody re-reads. Two
differences, both load-bearing:

1. **The risk gate is derived, not typed.** A node's `risk_gate` comes from
   the highest-risk environment it can touch. You cannot scaffold a graph
   whose guardrails disagree with its environment tiers, because there is
   only one place the tiers are stated.

2. **A research node exists when a mesh is declared.** An agent asked "why
   did cost go up" with no governed way to look is an agent that guesses.
   No mesh, no research node -- and the graph says why.
"""

from __future__ import annotations

from typing import Any

from bootstrap.config import PlatformConfig

_TIER_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


def _highest_tier(config: PlatformConfig) -> str:
    return max((e.risk_tier for e in config.environments), key=lambda t: _TIER_RANK[t])


def generate_graph(config: PlatformConfig) -> dict[str, Any]:
    highest = _highest_tier(config)
    autonomous = [e.name for e in config.environments if e.autonomous]
    gated = [e.name for e in config.environments if not e.autonomous]

    graph: dict[str, Any] = {
        "graph": {
            "version": "1.0",
            "type": "cloudops",
            "description": f"CloudOps agent graph -- {config.name}",
            "generated_from": ".github/config/platform.yml",
            "entrypoint": "cloudops-orchestrator",
        },
        "environments": {
            "promotion_path": config.promotion_path,
            "autonomous": autonomous,
            "human_gated": gated,
        },
        "nodes": {
            "cloudops-orchestrator": {
                "role": "Intake, routing, and environment gating",
                "zone": ["user-intent", "environment-selection", "ticket-linkage"],
                "tools": ["read", "search", "ask"],
                "risk_gate": "none",
                "rules": [
                    f"Never act on {gated} without explicit human approval in the transcript."
                    if gated else "All environments are autonomous at their declared tier.",
                    "State which environment you are acting on before acting on it.",
                ],
            },
            "iac-builder": {
                "role": f"{config.iac_tool} change authoring",
                "zone": ["iac-files", "module-calls", "variable-wiring"],
                "tools": ["read", "edit", "search", "terminal"],
                "risk_gate": highest,
                "rules": [
                    f"Every resource name carries its environment prefix ({[e.prefix for e in config.environments]}).",
                    "Never hardcode an account id, endpoint, or secret that belongs to another environment.",
                ],
            },
            "change-validator": {
                "role": "Correctness gate before any apply",
                "zone": ["naming", "env-leak-detection", "plan-review"],
                "tools": ["read", "search", "grep"],
                "risk_gate": "none",
                "gate": "must-pass",
                "rules": [
                    "A plan that touches a resource outside the named environment is a leak, not a diff.",
                    "Reachable from iac-builder on every change. There is no fast path around this node.",
                ],
            },
            "sre-observer": {
                "role": "Post-deploy health and incident triage",
                "zone": ["health-checks", "log-analysis", "incident-correlation"],
                "tools": ["read", "search", "terminal"],
                "risk_gate": "none",
                "rules": [
                    "'Flake' is a conclusion, not a diagnosis. Name the mechanism or say you could not find it.",
                ],
            },
        },
        "edges": [
            {"from": "cloudops-orchestrator", "to": "iac-builder", "trigger": "build | edit | scaffold"},
            {"from": "cloudops-orchestrator", "to": "sre-observer", "trigger": "triage | health | incident"},
            {"from": "iac-builder", "to": "change-validator", "trigger": "always", "gate": "must-pass"},
            {"from": "change-validator", "to": "iac-builder", "trigger": "violations && auto_fixable", "max_cycles": 3},
        ],
    }

    if config.mesh:
        graph["nodes"]["mesh-researcher"] = {
            "role": "Governed cross-plane data research",
            "zone": ["catalog-navigation", "sandboxed-reads", "evidence"],
            "tools": [
                "sandbox_create", "catalog_search", "catalog_describe", "catalog_lineage",
                "query_product", "unified_query", "research_plan", "research_run", "research_report",
            ],
            "risk_gate": "sandbox-policy",
            "mcp_server": "sandbox-research",
            "rules": [
                "Open one sandbox per question, scoped to the narrowest plane set that answers it.",
                "A classification refusal means narrow the read, never widen the sandbox.",
                "Every finding cites an evidence digest. An uncited claim is deleted, not hedged.",
                "The sandbox is a policy and spend envelope with an audit ledger, not kernel isolation.",
            ],
            "sandbox_defaults": dict(config.research_sandbox_defaults),
            "planes": sorted({m.plane for m in config.mesh}),
        }
        graph["edges"] += [
            {"from": "cloudops-orchestrator", "to": "mesh-researcher", "trigger": "why | investigate | cost | trend"},
            {"from": "sre-observer", "to": "mesh-researcher", "trigger": "incident needs historical context"},
            {"from": "mesh-researcher", "to": "cloudops-orchestrator", "trigger": "report delivered -> destroy sandbox"},
        ]
    else:
        graph["graph"]["research"] = (
            "No mesh planes declared in platform.yml, so no mesh-researcher node was "
            "generated. Add a `mesh:` section to give the graph a governed way to look "
            "at data instead of guessing."
        )

    graph["error_handling"] = {
        "decontamination": {
            "on_node_failure": "snapshot the failure, isolate it, do not carry the failed context forward",
            "on_retry": "reload configuration and state fresh; never reuse a stale plan",
            "max_error_chain": 3,
            "after_max": "stop and escalate with the partial result and the failures named",
        },
    }
    graph["failure_policy"] = {
        "default": {"retry": 1, "fallback": "escalate to a human with what is known"},
        "per_node": {
            "iac-builder": {"retry": 2, "fallback": "propose the diff, do not apply"},
            "change-validator": {"retry": 0, "fallback": "refuse the change"},
            "sre-observer": {"retry": 1, "fallback": "escalate with raw evidence"},
            **({"mesh-researcher": {"retry": 1, "fallback": "report the gap, do not guess"}} if config.mesh else {}),
        },
    }
    graph["governance"] = {
        "human_gate": {
            "enabled": bool(gated),
            "environments": gated,
            "description": "Changes to these environments require explicit human approval.",
        },
        "validation_gate": {
            "enabled": True,
            "description": "change-validator must pass before any apply. No node may bypass it.",
        },
        "evidence_gate": {
            "enabled": bool(config.mesh),
            "description": "No data claim without a matching evidence digest.",
        },
    }
    return graph


def generate_mesh_manifest(config: PlatformConfig) -> dict[str, Any]:
    """The `SANDBOX_RESEARCH_MESH` manifest for the sandbox-research server."""
    return {
        "connectors": [
            {"kind": m.connector, "name": m.name, "domain": m.domain, "config": dict(m.config)}
            for m in config.mesh
        ]
    }
