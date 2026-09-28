"""Agent adapter base class.

Ported from ``bootstrap/agent_adapters/_base.py`` in the
iac-sre-custom-coding-agent-framework this project started from. There, an
adapter's job was to translate a ``PlatformConfig`` into IDE-native
configuration files for one coding tool (Copilot, Cursor, Claude Code,
Continue.dev). The job is the same here, translating OpenCloudOps's own
``PlatformContract`` instead -- an adapter scaffolds *this* framework's
`occ` CLI into a target repository, in whatever shape that repo's coding
tool expects.

The distinction from ``plugin/opencloudops/`` matters: the plugin is a
Claude-Code-specific, marketplace-installable experience with skills, an
agent, and a hook. An adapter is portable scaffolding -- flat files written
directly into any target repo, readable by five different tools, none of
which needs to know OpenCloudOps ships a plugin at all.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from typing import TYPE_CHECKING

from opencloudops.spec import PlatformContract

if TYPE_CHECKING:
    from opencloudops.agentgraph import AgentGraphSpec


class AgentAdapter(ABC):
    """Base class for all agent adapters."""

    name: str = "base"

    @abstractmethod
    def generate_files(self, platform: PlatformContract, graph: "AgentGraphSpec | None" = None) -> dict[str, str]:
        """Return {target-relative path: content}.

        ``graph`` defaults to loading ``agent-graph.yaml`` from the current
        directory when omitted -- most callers running from an OpenCloudOps
        checkout never need to pass it explicitly.

        These are in addition to the agent-agnostic scaffold files from
        ``opencloudops.bootstrap.scaffold.collect_files``.
        """

    def write(
        self,
        platform: PlatformContract,
        target_dir: Path,
        graph: "AgentGraphSpec | None" = None,
        overwrite: bool = False,
    ) -> list[str]:
        from opencloudops.bootstrap.scaffold import write_files

        return write_files(self.generate_files(platform, graph), target_dir, overwrite=overwrite)


class AgnosticAdapter(AgentAdapter):
    """Minimal adapter for a team that uses none of the five supported tools.

    Adds nothing beyond the agent-agnostic scaffold files -- there is no
    IDE-native format to target, so this adapter's file set is empty and
    ``AGENTS.md`` (from ``scaffold.collect_files``) is the whole story.
    """

    name = "agnostic"

    def generate_files(self, platform: PlatformContract, graph: "AgentGraphSpec | None" = None) -> dict[str, str]:
        return {}
