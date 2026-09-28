"""Shared fixtures.

Everything is built from the repository's own contracts rather than from
hand-written doubles: if `platform/platform.yaml` or `patterns/` drifts into an
invalid state, the whole suite fails rather than passing against a mock that
still agrees with the old shape.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from opencloudops.agentgraph import AgentGraphSpec  # noqa: E402
from opencloudops.agents import PipelineDeps  # noqa: E402
from opencloudops.audit import memory_log  # noqa: E402
from opencloudops.graph import load_graph, load_platform, resolve  # noqa: E402
from opencloudops.ground import PatternCatalog  # noqa: E402
from opencloudops.learner import Learner  # noqa: E402


@pytest.fixture(scope="session")
def repo() -> Path:
    return REPO


@pytest.fixture(scope="session")
def platform():
    return load_platform(REPO / "platform" / "platform.yaml")


@pytest.fixture(scope="session")
def catalog():
    return PatternCatalog.from_dir(REPO / "patterns")


@pytest.fixture(scope="session")
def agent_spec():
    return AgentGraphSpec.load(REPO / "agent-graph.yaml")


@pytest.fixture
def reference_graph():
    return load_graph(REPO / "graphs" / "reference-web-service.graph.yaml")


@pytest.fixture
def dev_plan(reference_graph, platform, catalog):
    return resolve(reference_graph, platform, catalog, "dev")


@pytest.fixture
def prod_plan(reference_graph, platform, catalog):
    return resolve(reference_graph, platform, catalog, "prod")


@pytest.fixture
def deps(platform, catalog, tmp_path):
    """Isolated per test: an in-memory audit log and a temporary memory store,
    so a test can never write into the developer's real memories/."""
    return PipelineDeps(
        platform=platform,
        catalog=catalog,
        learner=Learner(memory_dir=tmp_path / "memories"),
        audit=memory_log(),
        graphs_dir=REPO / "graphs",
    )
