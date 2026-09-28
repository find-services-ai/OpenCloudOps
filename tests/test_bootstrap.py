"""The bootstrap adapter system: scaffolding OpenCloudOps into a target repo
for five coding tools, and the graph safety invariants that back it.
"""

from __future__ import annotations

import copy

import pytest
import yaml

from opencloudops.agentgraph import AgentGraphSpec
from opencloudops.bootstrap.adapters import ADAPTER_NAMES, get_adapter
from opencloudops.bootstrap.agent_specs import build_agent_specs
from opencloudops.bootstrap.graph_invariants import (
    GraphInvariantError,
    graph_invariant_errors,
    validate_graph_invariants,
)
from opencloudops.bootstrap.sdk import Bootstrap


class TestAgentSpecs:
    def test_one_spec_per_graph_node(self, agent_spec):
        specs = build_agent_specs(agent_spec)
        assert {s.name for s in specs} == set(agent_spec.nodes)

    def test_read_only_matches_the_tool_list_not_a_label(self, agent_spec):
        """read_only is derived from tools, not declared -- a node cannot claim
        to be read-only while keeping a write-capable tool."""
        specs = {s.name: s for s in build_agent_specs(agent_spec)}
        assert specs["iac-validator"].read_only is True
        assert specs["sre-observer"].read_only is True
        assert specs["orchestrator"].read_only is True
        assert specs["iac-builder"].read_only is False

    def test_mcp_servers_are_derived_from_tools(self, agent_spec):
        specs = {s.name: s for s in build_agent_specs(agent_spec)}
        assert specs["sre-observer"].mcp_servers == ["cloud-logs", "observability"]
        assert specs["orchestrator"].mcp_servers == []

    def test_body_never_omits_the_never_claim_applied_rule(self, agent_spec):
        specs = build_agent_specs(agent_spec)
        for s in specs:
            assert "no apply path" in s.body(specs)

    def test_a_graph_edit_changes_the_rendered_body(self, agent_spec):
        """The graph is the single source of truth -- there is no second,
        hand-maintained content dict to fall out of sync with it."""
        specs = build_agent_specs(agent_spec)
        before = next(s for s in specs if s.name == "iac-builder").body(specs)

        edited = agent_spec.model_copy(deep=True)
        edited.nodes["iac-builder"].description = "A totally different description for this test."
        after = next(s for s in build_agent_specs(edited) if s.name == "iac-builder").body(
            build_agent_specs(edited)
        )
        assert before != after
        assert "totally different description" in after


@pytest.mark.parametrize("agent", ADAPTER_NAMES)
class TestEveryAdapter:
    def test_generates_without_error(self, agent, platform, agent_spec):
        files = get_adapter(agent).generate_files(platform, agent_spec)
        assert isinstance(files, dict)

    def test_is_deterministic(self, agent, platform, agent_spec):
        first = get_adapter(agent).generate_files(platform, agent_spec)
        second = get_adapter(agent).generate_files(platform, agent_spec)
        assert first == second

    def test_paths_stay_relative_and_contained(self, agent, platform, agent_spec):
        for path in get_adapter(agent).generate_files(platform, agent_spec):
            assert not path.startswith("/"), path
            assert ".." not in path.split("/"), path

    def test_never_claims_anything_was_applied(self, agent, platform, agent_spec):
        """Every generated file, across every tool, repeats the one rule that
        must never drift: OpenCloudOps proposes, it never applies."""
        files = get_adapter(agent).generate_files(platform, agent_spec)
        if not files:
            pytest.skip("agnostic adapter adds nothing beyond AGENTS.md")
        blob = "\n".join(files.values())
        assert "no apply path" in blob or "no cloud credentials" in blob


class TestAdaptersRenderOneFilePerNode:
    """The structural correction this module exists to make: each tool gets
    one file per graph node, not a hand-authored summary of the graph."""

    def test_claude_code_one_subagent_per_node(self, platform, agent_spec):
        files = get_adapter("claude-code").generate_files(platform, agent_spec)
        for name in agent_spec.nodes:
            assert f".claude/agents/{name}.md" in files

    def test_cursor_one_rule_per_node(self, platform, agent_spec):
        files = get_adapter("cursor").generate_files(platform, agent_spec)
        for name in agent_spec.nodes:
            assert f".cursor/rules/{name}.mdc" in files

    def test_copilot_one_agent_file_per_node(self, platform, agent_spec):
        files = get_adapter("copilot").generate_files(platform, agent_spec)
        for name in agent_spec.nodes:
            assert f".github/agents/{name}.agent.md" in files

    def test_continue_one_rule_per_node(self, platform, agent_spec):
        files = get_adapter("continue").generate_files(platform, agent_spec)
        for name in agent_spec.nodes:
            assert f".continue/rules/{name}.md" in files

    def test_copilot_read_only_nodes_get_no_write_tool(self, platform, agent_spec):
        """The read/write boundary is a tool-list difference the target
        editor itself enforces, not just a line of prose asking nicely."""
        files = get_adapter("copilot").generate_files(platform, agent_spec)
        for name in ("orchestrator", "iac-validator", "sre-observer"):
            content = files[f".github/agents/{name}.agent.md"]
            assert "write_file" not in content

    def test_claude_code_read_only_nodes_declare_disallowed_tools(self, platform, agent_spec):
        files = get_adapter("claude-code").generate_files(platform, agent_spec)
        for name in ("orchestrator", "iac-validator", "sre-observer"):
            assert "disallowedTools:" in files[f".claude/agents/{name}.md"]
        assert "disallowedTools:" not in files[".claude/agents/iac-builder.md"]


class TestGraphInvariants:
    def test_the_shipped_graph_passes_clean(self, agent_spec):
        assert graph_invariant_errors(agent_spec) == []
        validate_graph_invariants(agent_spec)  # must not raise

    def _mutate(self, agent_spec, mutate):
        raw = yaml.safe_load(open("agent-graph.yaml"))
        mutate(raw)
        return AgentGraphSpec.model_validate(
            {
                "entry": raw["graph"]["entry"],
                "nodes": raw["nodes"],
                "edges": raw["edges"],
                "evals": raw.get("evals", {}),
                "failure_policy": raw.get("failure_policy", {}),
            }
        )

    def test_catches_a_write_tool_on_a_read_only_node(self):
        spec = self._mutate(None, lambda raw: raw["nodes"]["iac-validator"]["tools"].append("edit"))
        errors = graph_invariant_errors(spec)
        assert any("iac-validator" in e and "write-capable" in e for e in errors)

    def test_catches_a_high_gate(self):
        spec = self._mutate(None, lambda raw: raw["nodes"].__setitem__(
            "release-executor", {**raw["nodes"]["release-executor"], "risk_gate": "HIGH"}
        ))
        errors = graph_invariant_errors(spec)
        assert any("release-executor" in e and "HIGH" in e for e in errors)

    def test_catches_an_unbounded_repair_loop(self):
        def mutate(raw):
            for e in raw["edges"]:
                if e["from"] == "iac-validator" and e["to"] == "iac-builder":
                    del e["max_cycles"]

        spec = self._mutate(None, mutate)
        errors = graph_invariant_errors(spec)
        assert any("max_cycles" in e for e in errors)

    def test_catches_a_missing_must_pass_gate(self):
        def mutate(raw):
            for e in raw["edges"]:
                if e["from"] == "iac-builder" and e["to"] == "iac-validator":
                    e["gate"] = None

        spec = self._mutate(None, mutate)
        errors = graph_invariant_errors(spec)
        assert any("must-pass" in e for e in errors)

    def test_catches_a_genuinely_unreachable_node(self):
        spec = self._mutate(
            None, lambda raw: raw.__setitem__("edges", [e for e in raw["edges"] if e["to"] != "knowledge-curator"])
        )
        errors = graph_invariant_errors(spec)
        assert any("knowledge-curator" in e for e in errors)

    def test_an_alternate_path_is_not_a_false_positive(self):
        """Removing one edge into a node that has a second inbound edge must
        not be reported as unreachable -- the check follows the whole graph,
        not just direct orchestrator edges."""
        spec = self._mutate(
            None,
            lambda raw: raw.__setitem__(
                "edges",
                [e for e in raw["edges"] if not (e["from"] == "orchestrator" and e["to"] == "knowledge-curator")],
            ),
        )
        errors = graph_invariant_errors(spec)
        assert not any("knowledge-curator" in e for e in errors)

    def test_catches_inverted_token_budgets(self):
        spec = self._mutate(None, lambda raw: raw["evals"].__setitem__("per_node_warn", 999_999))
        errors = graph_invariant_errors(spec)
        assert any("token" not in e and "per_node_warn" in e for e in errors) or any(
            "per_node_warn" in e for e in errors
        )

    def test_validate_raises_with_every_error_attached(self):
        spec = self._mutate(None, lambda raw: raw["nodes"]["iac-validator"]["tools"].append("edit"))
        with pytest.raises(GraphInvariantError) as exc_info:
            validate_graph_invariants(spec)
        assert len(exc_info.value.errors) >= 1


class TestBootstrapSDK:
    def test_run_writes_agent_agnostic_plus_adapter_files(self, platform, agent_spec, tmp_path):
        b = Bootstrap(platform, target_dir=tmp_path, agent="claude-code", graph=agent_spec)
        written = b.run()
        assert "AGENTS.md" in written
        assert "CLAUDE.md" in written
        assert (tmp_path / "AGENTS.md").exists()
        assert (tmp_path / ".claude" / "agents" / "orchestrator.md").exists()

    def test_validate_lists_conflicts_before_writing_anything(self, platform, agent_spec, tmp_path):
        (tmp_path / "AGENTS.md").write_text("pre-existing")
        b = Bootstrap(platform, target_dir=tmp_path, agent="agnostic", graph=agent_spec)
        assert b.validate() == ["AGENTS.md"]

    def test_run_refuses_to_clobber_without_overwrite(self, platform, agent_spec, tmp_path):
        (tmp_path / "AGENTS.md").write_text("pre-existing, must survive")
        b = Bootstrap(platform, target_dir=tmp_path, agent="agnostic", graph=agent_spec)
        with pytest.raises(FileExistsError):
            b.run()
        assert (tmp_path / "AGENTS.md").read_text() == "pre-existing, must survive"

    def test_overwrite_true_replaces_it(self, platform, agent_spec, tmp_path):
        (tmp_path / "AGENTS.md").write_text("stale")
        b = Bootstrap(platform, target_dir=tmp_path, agent="agnostic", overwrite=True, graph=agent_spec)
        b.run()
        assert (tmp_path / "AGENTS.md").read_text() != "stale"

    def test_dry_run_touches_no_filesystem(self, platform, agent_spec, tmp_path):
        b = Bootstrap(platform, target_dir=tmp_path, agent="cursor", graph=agent_spec)
        files = b.dry_run()
        assert files
        assert list(tmp_path.iterdir()) == []

    def test_appends_gitignore_once(self, platform, agent_spec, tmp_path):
        b = Bootstrap(platform, target_dir=tmp_path, agent="agnostic", graph=agent_spec)
        b.run()
        first = (tmp_path / ".gitignore").read_text()
        b2 = Bootstrap(platform, target_dir=tmp_path, agent="agnostic", overwrite=True, graph=agent_spec)
        b2.run()
        assert (tmp_path / ".gitignore").read_text() == first
        assert first.count("# OpenCloudOps") == 1

    def test_unknown_agent_is_refused(self, platform, agent_spec, tmp_path):
        with pytest.raises(ValueError, match="Unknown agent"):
            get_adapter("some-editor-that-does-not-exist")
