from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bootstrap import PlatformConfig, plan, write
from bootstrap.cli import main as cli_main
from bootstrap.config import ConfigError, Environment, minimal_profile, playground_profile
from bootstrap.graph import generate_graph, generate_mesh_manifest
from bootstrap.scaffold import conflicts


def test_high_risk_environment_cannot_be_autonomous():
    """The one rule no config may relax. If a YAML key could turn this off,
    the guardrail would be decorative."""
    with pytest.raises(ConfigError, match="cannot be autonomous"):
        Environment("prod", "prod", "p1", "HIGH", autonomous=True)


def test_promotion_path_is_derived_from_risk_tiers_not_declared():
    config = playground_profile()
    assert config.promotion_path == ["dev", "staging", "prod"]


def test_duplicate_environment_names_are_refused():
    with pytest.raises(ConfigError, match="duplicate"):
        PlatformConfig(environments=[Environment("dev", "dev", "d1"), Environment("dev", "dev2", "d2")])


def test_at_least_one_environment_is_required():
    with pytest.raises(ConfigError, match="at least one environment"):
        PlatformConfig(environments=[])


def test_unknown_agent_is_refused():
    with pytest.raises(ConfigError, match="agent must be one of"):
        PlatformConfig(agent="clippy", environments=[Environment("dev", "dev", "d1")])


def test_graph_risk_gate_is_derived_from_the_highest_tier():
    assert generate_graph(playground_profile())["nodes"]["iac-builder"]["risk_gate"] == "HIGH"
    assert generate_graph(minimal_profile())["nodes"]["iac-builder"]["risk_gate"] == "LOW"


def test_a_mesh_produces_a_research_node_and_no_mesh_explains_its_absence():
    with_mesh = generate_graph(playground_profile())
    assert "mesh-researcher" in with_mesh["nodes"]
    assert with_mesh["nodes"]["mesh-researcher"]["mcp_server"] == "sandbox-research"

    without = generate_graph(minimal_profile())
    assert "mesh-researcher" not in without["nodes"]
    assert "No mesh planes declared" in without["graph"]["research"]


def test_validation_gate_is_always_on():
    for config in (playground_profile(), minimal_profile()):
        graph = generate_graph(config)
        assert graph["governance"]["validation_gate"]["enabled"] is True
        assert graph["nodes"]["change-validator"]["gate"] == "must-pass"
        # No edge may reach an apply without passing through the validator.
        assert any(
            e["from"] == "iac-builder" and e["to"] == "change-validator" and e.get("gate") == "must-pass"
            for e in graph["edges"]
        )


def test_human_gate_lists_exactly_the_non_autonomous_environments():
    graph = generate_graph(playground_profile())
    assert graph["governance"]["human_gate"]["environments"] == ["staging", "prod"]


def test_mesh_manifest_matches_the_sandbox_research_connector_contract():
    manifest = generate_mesh_manifest(playground_profile())
    assert {c["kind"] for c in manifest["connectors"]} == {"sql_warehouse", "object_lake", "bi"}
    for connector in manifest["connectors"]:
        assert set(connector) == {"kind", "name", "domain", "config"}


def test_scaffold_writes_a_config_that_reloads_to_the_same_plan(tmp_path):
    config = playground_profile()
    written = write(config, tmp_path)
    assert "CLAUDE.md" in written and "agent-graph.yml" in written and "mesh.yaml" in written

    reloaded = PlatformConfig.from_yaml(tmp_path / ".github" / "config" / "platform.yml")
    assert plan(reloaded) == plan(config)


def test_scaffold_refuses_to_overwrite_unless_asked(tmp_path):
    config = minimal_profile()
    write(config, tmp_path)
    assert conflicts(config, tmp_path)
    with pytest.raises(FileExistsError, match="overwrite"):
        write(config, tmp_path)
    assert write(config, tmp_path, overwrite=True)


def test_every_scaffolded_yaml_parses(tmp_path):
    write(playground_profile(), tmp_path)
    for rel in plan(playground_profile()):
        if rel.endswith((".yml", ".yaml")):
            assert yaml.safe_load((tmp_path / rel).read_text()), rel


def test_every_scaffolded_skill_has_frontmatter(tmp_path):
    write(playground_profile(), tmp_path)
    for path in Path(tmp_path).rglob("SKILL.md"):
        text = path.read_text()
        assert text.startswith("---\n"), path
        assert "name:" in text and "description:" in text, path


def test_scaffolded_guidance_does_not_overclaim_isolation(tmp_path):
    """Any document that describes the sandbox must not call it a VM or a
    container -- that mislabelling is how untrusted code gets a shell."""
    write(playground_profile(), tmp_path)
    forbidden = ("is a virtual machine", "runs in a container", "fully isolated", "kernel-level isolation")
    for path in Path(tmp_path).rglob("*.md"):
        lowered = path.read_text().lower()
        for phrase in forbidden:
            assert phrase not in lowered, f"{path} contains {phrase!r}"


def test_agnostic_agent_still_gets_an_entrypoint_file(tmp_path):
    config = playground_profile()
    config.agent = "agnostic"
    files = plan(config)
    assert "AGENTS.md" in files
    assert "CLAUDE.md" not in files


def test_cli_dry_run_writes_nothing(tmp_path, capsys):
    assert cli_main(["--target", str(tmp_path), "--dry-run"]) == 0
    assert "nothing written" in capsys.readouterr().out
    assert not list(tmp_path.iterdir())


def test_cli_refuses_to_clobber_and_says_so(tmp_path, capsys):
    assert cli_main(["--target", str(tmp_path), "--profile", "minimal"]) == 0
    assert cli_main(["--target", str(tmp_path), "--profile", "minimal"]) == 1
    assert "refusing to overwrite" in capsys.readouterr().err


def test_cli_revalidates_after_an_agent_override(tmp_path, capsys):
    assert cli_main(["--target", str(tmp_path), "--agent", "copilot", "--dry-run"]) == 0
    assert cli_main(["--target", str(tmp_path), "--config", "/does/not/exist.yml"]) == 2
