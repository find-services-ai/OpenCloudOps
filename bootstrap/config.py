"""Platform configuration for a CloudOps agentic-AI repository.

One dataclass, three shipped profiles, and a YAML loader. Everything the
bootstrap writes -- the agent graph, the skills, the guardrails, the mesh
manifest -- derives from this object, so "change the platform" is editing
one file rather than grepping for account ids across a scaffold.

The shape is adapted from the IaC/SRE bootstrap referenced in the brief.
What is added here is `mesh` and `research`: a CloudOps platform that an
agent is expected to reason about needs its data planes declared with the
same seriousness as its environments, or the agent is reasoning from
whatever it happens to stumble into.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

RISK_TIERS = ("LOW", "MEDIUM", "HIGH")
AGENTS = ("claude-code", "copilot", "cursor", "agnostic")


class ConfigError(ValueError):
    """Raised for a configuration that would scaffold a repo nobody can use."""


@dataclass
class Environment:
    name: str
    branch: str
    prefix: str
    risk_tier: str = "LOW"
    autonomous: bool = True

    def __post_init__(self) -> None:
        if self.risk_tier not in RISK_TIERS:
            raise ConfigError(f"{self.name}: risk_tier must be one of {RISK_TIERS}, got {self.risk_tier!r}")
        if self.risk_tier == "HIGH" and self.autonomous:
            # The one rule that is not negotiable by config. An autonomous
            # agent on a HIGH-risk environment is the failure mode this whole
            # framework exists to prevent; allowing it via a YAML key would
            # make the guardrail decorative.
            raise ConfigError(
                f"{self.name}: a HIGH risk_tier environment cannot be autonomous. "
                "Set autonomous: false, or lower the tier deliberately."
            )


@dataclass
class MeshPlane:
    """One data plane the research agent may reach."""

    plane: str  # warehouse | lake | bi | api
    connector: str  # sandbox-research connector type
    name: str
    domain: str
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlatformConfig:
    name: str = "OpenCloudOps Playground"
    application: str = "cloudops"
    cloud: str = "aws"
    iac_tool: str = "terraform"
    compute: str = "ecs_fargate"
    agent: str = "claude-code"
    environments: list[Environment] = field(default_factory=list)
    mesh: list[MeshPlane] = field(default_factory=list)
    research_sandbox_defaults: dict[str, Any] = field(
        default_factory=lambda: {
            "ttl_seconds": 1800,
            "max_queries": 40,
            "max_rows": 25_000,
            "allow_classifications": ["public", "internal"],
            "network": "none",
        }
    )

    def __post_init__(self) -> None:
        if self.agent not in AGENTS:
            raise ConfigError(f"agent must be one of {AGENTS}, got {self.agent!r}")
        if not self.environments:
            raise ConfigError("at least one environment is required")
        names = [e.name for e in self.environments]
        if len(names) != len(set(names)):
            raise ConfigError(f"duplicate environment names: {names}")

    @property
    def promotion_path(self) -> list[str]:
        """Environments ordered lowest to highest risk. This is the promotion
        chain the agent may walk, and it is derived rather than declared so it
        cannot disagree with the risk tiers it is built from."""
        order = {tier: i for i, tier in enumerate(RISK_TIERS)}
        return [e.name for e in sorted(self.environments, key=lambda e: (order[e.risk_tier], e.name))]

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": {
                "name": self.name,
                "application": self.application,
                "cloud": self.cloud,
                "iac_tool": self.iac_tool,
                "compute": self.compute,
                "agent": self.agent,
            },
            "environments": [asdict(e) for e in self.environments],
            "promotion_path": self.promotion_path,
            "mesh": [asdict(m) for m in self.mesh],
            "research_sandbox_defaults": dict(self.research_sandbox_defaults),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlatformConfig":
        p = data.get("platform", {})
        return cls(
            name=p.get("name", "OpenCloudOps Playground"),
            application=p.get("application", "cloudops"),
            cloud=p.get("cloud", "aws"),
            iac_tool=p.get("iac_tool", "terraform"),
            compute=p.get("compute", "ecs_fargate"),
            agent=p.get("agent", "claude-code"),
            environments=[Environment(**e) for e in data.get("environments", [])],
            mesh=[MeshPlane(**m) for m in data.get("mesh", [])],
            research_sandbox_defaults=data.get("research_sandbox_defaults")
            or cls().research_sandbox_defaults,
        )

    @classmethod
    def from_yaml(cls, path: Path | str) -> "PlatformConfig":
        import yaml

        return cls.from_dict(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})


def playground_profile() -> PlatformConfig:
    """The profile the labs run against: three environments and the synthetic
    CloudOps mesh from ideas/sandbox-research/fixtures/seed_mesh.py."""
    return PlatformConfig(
        name="OpenCloudOps Playground",
        environments=[
            Environment("dev", "dev", "d1", "LOW", autonomous=True),
            Environment("staging", "staging", "s1", "MEDIUM", autonomous=False),
            Environment("prod", "prod", "p1", "HIGH", autonomous=False),
        ],
        mesh=[
            MeshPlane("warehouse", "sql_warehouse", "finops-warehouse", "finops",
                      {"database": "./mesh/warehouse/finops.db",
                       "classifications": {"service_inventory.owner_email": "pii"}}),
            MeshPlane("lake", "object_lake", "cloudops-lake", "cloudops",
                      {"root": "./mesh/lake",
                       "classifications": {"deploy_events.approver_email": "pii"}}),
            MeshPlane("bi", "bi", "exec-bi", "reporting",
                      {"definitions": "./mesh/bi/dashboards.yaml"}),
        ],
    )


def minimal_profile() -> PlatformConfig:
    """Single environment, no mesh -- for teaching the graph before the data."""
    return PlatformConfig(
        name="CloudOps Minimal",
        environments=[Environment("dev", "dev", "d1", "LOW", autonomous=True)],
    )


PROFILES = {"playground": playground_profile, "minimal": minimal_profile}
