from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
for extra in (REPO_ROOT, REPO_ROOT / "labs", REPO_ROOT / "evaluation"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))


def _sandbox_research_available() -> bool:
    try:
        from _harness import sandbox_research_path

        sandbox_research_path()
        return True
    except Exception:
        return False


#

# The labs and the evaluator drive the real sandbox-research implementation
# from a sibling checkout. Skipping (rather than vendoring a copy) keeps the
# bootstrap tests runnable standalone while the integration tests still fail
# loudly when the two repos drift apart.
requires_sandbox_research = pytest.mark.skipif(
    not _sandbox_research_available(),
    reason="sandbox-research not found; set SANDBOX_RESEARCH_PATH or clone mcp-idea-publisher alongside this repo",
)


@pytest.fixture(scope="session")
def lab_env(tmp_path_factory):
    from _harness import lab_platform

    platform, call = lab_platform(tmp_path_factory.mktemp("mesh"))
    return platform, call
