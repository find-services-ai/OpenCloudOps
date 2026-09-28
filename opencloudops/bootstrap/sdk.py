"""Public bootstrap SDK: scaffold OpenCloudOps into a target repository.

Ported from ``bootstrap/sdk.py``. Same public shape --
``Bootstrap(platform, target_dir).validate()/.dry_run()/.run()`` -- so
anything written against the original SDK's API needs only its import
changed, not its calling convention. The one substantive difference:
``platform`` here is OpenCloudOps's own ``PlatformContract`` (loaded from
``platform/platform.yaml``) rather than a separate ``PlatformConfig``
this project would otherwise have to keep in sync with it.

    from opencloudops.bootstrap.sdk import Bootstrap
    from opencloudops.graph import load_platform

    platform = load_platform("platform/platform.yaml")
    b = Bootstrap(platform, target_dir="../my-service-repo", agent="cursor")
    conflicts = b.validate()   # paths that already exist in the target
    files = b.dry_run()        # {path: content}, nothing written
    written = b.run()          # writes files, returns paths written
"""

from __future__ import annotations

from pathlib import Path

from opencloudops.bootstrap.adapters import get_adapter
from opencloudops.bootstrap.scaffold import append_gitignore, collect_files, write_files
from typing import TYPE_CHECKING

from opencloudops.spec import PlatformContract

if TYPE_CHECKING:
    from opencloudops.agentgraph import AgentGraphSpec


class Bootstrap:
    """Scaffolding engine: agent-agnostic files plus one adapter's files.

    Parameters
    ----------
    platform:
        The platform contract to scaffold (from ``platform/platform.yaml``).
    target_dir:
        Repository root to write into. Defaults to the current directory.
    agent:
        One of ``opencloudops.bootstrap.adapters.ADAPTER_NAMES``.
    overwrite:
        If True, existing files in the target are replaced. Default False --
        a conflict raises ``FileExistsError`` from ``run()`` rather than
        silently clobbering something the target repo already has.
    """

    def __init__(
        self,
        platform: PlatformContract,
        target_dir: str | Path = ".",
        agent: str = "agnostic",
        graph: "AgentGraphSpec | None" = None,
        overwrite: bool = False,
    ) -> None:
        self._platform = platform
        self._target = Path(target_dir).resolve()
        self._agent = agent
        self._graph = graph
        self._overwrite = overwrite

    def _all_files(self) -> dict[str, str]:
        files = collect_files(self._platform)
        files.update(get_adapter(self._agent).generate_files(self._platform, self._graph))
        return files

    def validate(self) -> list[str]:
        """Relative paths that already exist in the target and would be
        overwritten by ``run()``. Empty means safe to proceed."""
        all_files = self._all_files()
        return [rel for rel in all_files if (self._target / rel).exists()]

    def dry_run(self) -> dict[str, str]:
        """Every file this run would write, without touching the filesystem."""
        return self._all_files()

    def run(self) -> list[str]:
        """Write every file into target_dir. Returns the paths written."""
        written = write_files(self._all_files(), self._target, overwrite=self._overwrite)
        append_gitignore(self._target)
        return written

    @property
    def target_dir(self) -> Path:
        return self._target

    @property
    def agent(self) -> str:
        return self._agent

    def __repr__(self) -> str:
        return f"Bootstrap(org={self._platform.org!r}, agent={self._agent!r}, target={self._target})"
