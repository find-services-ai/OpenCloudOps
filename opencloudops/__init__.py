"""OpenCloudOps -- an agentic-first cloud operations framework.

The framework is a *graph of agents* that turns an idea into grounded,
guarded, provider-specific infrastructure-as-code plus the SRE setup to
run it. Nothing here executes against a cloud account: every agent
produces a proposal, and a human (or an explicitly authorised automation)
applies it.

Read ``docs/ARCHITECTURE.md`` for the design, and ``opencloudops/spec.py``
for the contracts every agent is bound by.
"""

__version__ = "0.1.0"

from opencloudops.spec import (  # noqa: F401
    PlatformContract,
    ResourceGraph,
    ResolvedPlan,
    RiskTier,
)

__all__ = ["PlatformContract", "ResourceGraph", "ResolvedPlan", "RiskTier", "__version__"]
