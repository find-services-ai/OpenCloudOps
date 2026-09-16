"""The labs are teaching material, which makes them the thing most likely to
rot silently: nobody notices a broken lab until a student hits it. Each one
runs here as a subprocess and must exit 0 with every internal check passing."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import requires_sandbox_research

REPO_ROOT = Path(__file__).resolve().parents[1]
LABS = sorted((REPO_ROOT / "labs").glob("lab*.py"))


def test_the_labs_exist_and_are_numbered():
    assert [p.name.split("_")[0] for p in LABS] == [f"lab{i:02d}" for i in range(1, len(LABS) + 1)]


@requires_sandbox_research
@pytest.mark.parametrize("lab", LABS, ids=[p.stem for p in LABS])
def test_lab_runs_clean(lab, tmp_path):
    result = subprocess.run(
        [sys.executable, str(lab)],
        capture_output=True, text=True, timeout=180, cwd=REPO_ROOT,
    )
    # Exit code and the summary line are the signal, not the absence of the
    # string "[FAIL]": lab05 legitimately PRINTS a failing rubric row, because
    # showing a run that the rubric rejects is the point of that lab.
    assert result.returncode == 0, (result.stdout[-2000:], result.stderr[-1500:])
    assert "All checks passed." in result.stdout, result.stdout[-2000:]


@requires_sandbox_research
def test_the_evaluator_cli_demo_runs_clean():
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "evaluation" / "evaluate.py"), "--demo"],
        capture_output=True, text=True, timeout=180, cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert "TRUSTWORTHY" in result.stdout


def test_lab_harness_gives_an_actionable_error_when_the_implementation_is_missing(monkeypatch):
    import _harness

    monkeypatch.setattr(_harness, "_CANDIDATES", [Path("/definitely/not/here")])
    with pytest.raises(_harness.LabSetupError) as exc:
        _harness.sandbox_research_path()
    assert "SANDBOX_RESEARCH_PATH" in str(exc.value)
    assert "mcp-idea-publisher" in str(exc.value)
