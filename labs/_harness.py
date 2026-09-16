"""Shared setup for the labs.

The labs drive the real `sandbox-research` MCP tool surface, not a mock.
They import it directly rather than speaking MCP over stdio for one
reason: when a lab fails, a student should see a Python traceback pointing
at the tool call they got wrong, not a JSON-RPC error. `handle_tool` is the
same function the MCP server dispatches to, so the behaviour is identical.

Resolution order for the implementation:
  1. $SANDBOX_RESEARCH_PATH
  2. ../mcp-idea-publisher/ideas/sandbox-research (sibling checkout)
  3. ~/mcp-idea-publisher/ideas/sandbox-research
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

_CANDIDATES = [
    Path(os.environ["SANDBOX_RESEARCH_PATH"]) if os.environ.get("SANDBOX_RESEARCH_PATH") else None,
    REPO_ROOT.parent / "mcp-idea-publisher" / "ideas" / "sandbox-research",
    Path.home() / "mcp-idea-publisher" / "ideas" / "sandbox-research",
]


class LabSetupError(RuntimeError):
    pass


def sandbox_research_path() -> Path:
    for candidate in _CANDIDATES:
        if candidate and (candidate / "src" / "tools.py").exists():
            return candidate
    raise LabSetupError(
        "Could not find the sandbox-research implementation. Clone "
        "find-services-ai/mcp-idea-publisher next to this repo, or set "
        "SANDBOX_RESEARCH_PATH to <checkout>/ideas/sandbox-research.\n"
        f"Looked in: {[str(c) for c in _CANDIDATES if c]}"
    )


def _import_platform():
    path = sandbox_research_path()
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
    from fixtures.seed_mesh import build, connector_specs  # noqa: PLC0415
    from src.platform import ResearchPlatform  # noqa: PLC0415
    from src.tools import handle_tool  # noqa: PLC0415

    return build, connector_specs, ResearchPlatform, handle_tool


def lab_platform(mesh_dir: str | Path | None = None, *, db: str = ":memory:"):
    """Build the synthetic mesh, register its connectors, sync the catalog.

    Returns `(platform, call)` where `call(tool_name, **kwargs)` invokes a
    tool exactly as an agent would.
    """
    build, connector_specs, ResearchPlatform, handle_tool = _import_platform()

    target = Path(mesh_dir or (REPO_ROOT / "mesh"))
    paths = build(target)

    platform = ResearchPlatform(db_path=db)
    for spec in connector_specs(paths):
        platform.register_connector(**spec)
    platform.sync()

    def call(tool: str, **kwargs: Any) -> dict[str, Any]:
        return handle_tool(platform, tool, kwargs)

    return platform, call


def show(label: str, payload: Any, limit: int = 1200) -> None:
    import json

    print(f"\n--- {label} " + "-" * max(0, 58 - len(label)))
    text = json.dumps(payload, indent=2, default=str)
    print(text if len(text) <= limit else text[:limit] + f"\n... ({len(text)} bytes total)")


def check(condition: bool, message: str) -> None:
    """Lab assertion. Prints the outcome rather than raising, so a lab keeps
    running and a student sees every check, not just the first failure."""
    print(f"  [{'PASS' if condition else 'FAIL'}] {message}")
    if not condition:
        _FAILURES.append(message)


_FAILURES: list[str] = []


def summary() -> int:
    if _FAILURES:
        print(f"\n{len(_FAILURES)} check(s) failed:")
        for f in _FAILURES:
            print(f"  - {f}")
        return 1
    print("\nAll checks passed.")
    return 0
