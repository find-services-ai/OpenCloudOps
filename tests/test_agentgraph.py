"""The agent graph runtime: zones, gates, repair cycles, budgets, telemetry."""

from __future__ import annotations

import copy

import pytest
import yaml

from opencloudops.agentgraph import (
    AgentContext,
    AgentGraphSpec,
    AgentRuntime,
    NodeResult,
    NodeStatus,
    TokenBudgetExceeded,
)
from opencloudops.agents import build_runtime
from opencloudops.audit import memory_log
from opencloudops.spec import RiskTier


def _minimal_spec(**overrides) -> AgentGraphSpec:
    body = {
        "entry": "a",
        "nodes": {
            "a": {"role": "first", "zone": ["one"]},
            "b": {"role": "second", "zone": ["two"], "risk_gate": "MEDIUM"},
        },
        "edges": [{"from": "a", "to": "b", "trigger": "always"}],
    }
    body.update(overrides)
    return AgentGraphSpec.model_validate(body)


class TestSpecValidation:
    def test_repository_graph_loads(self, agent_spec):
        assert agent_spec.entry in agent_spec.nodes
        assert len(agent_spec.nodes) == 6

    def test_overlapping_zones_are_refused(self):
        """Two agents owning one concern is how they collide on one file."""
        with pytest.raises(ValueError, match="claimed by both"):
            AgentGraphSpec.model_validate(
                {
                    "entry": "a",
                    "nodes": {
                        "a": {"role": "x", "zone": ["shared"]},
                        "b": {"role": "y", "zone": ["shared"]},
                    },
                }
            )

    def test_edge_to_an_undeclared_node_is_refused(self):
        with pytest.raises(ValueError, match="unknown node"):
            AgentGraphSpec.model_validate(
                {
                    "entry": "a",
                    "nodes": {"a": {"role": "x"}},
                    "edges": [{"from": "a", "to": "ghost"}],
                }
            )

    def test_unparseable_risk_gate_is_refused(self):
        with pytest.raises(ValueError, match="risk_gate"):
            AgentGraphSpec.model_validate(
                {"entry": "a", "nodes": {"a": {"role": "x", "risk_gate": "SORT-OF"}}}
            )

    def test_trigger_alternatives_are_split(self, agent_spec):
        edge = next(e for e in agent_spec.edges if e.to == "iac-builder" and e.from_ == "orchestrator")
        assert "provision" in edge.triggers
        assert edge.matches("provision") and not edge.matches("triage")

    def test_repair_edges_route_on_verdict_not_intent(self, agent_spec):
        repair = next(e for e in agent_spec.edges if e.max_cycles is not None)
        assert repair.matches("plan"), "a repair edge must be reachable whatever the user asked for"


class TestGates:
    def test_a_gated_node_is_held_without_approval(self):
        runtime = AgentRuntime(_minimal_spec(), audit=memory_log(), autonomous_up_to=RiskTier.low)
        runtime.register("a")(lambda ctx: NodeResult(node="a", status=NodeStatus.ok))
        runtime.register("b")(lambda ctx: NodeResult(node="b", status=NodeStatus.ok))
        run = runtime.run("go")
        assert run.status is NodeStatus.awaiting_approval
        assert run.approvals_required == ["b"]

    def test_an_approval_releases_it(self):
        runtime = AgentRuntime(_minimal_spec(), audit=memory_log(), autonomous_up_to=RiskTier.low)
        runtime.register("a")(lambda ctx: NodeResult(node="a", status=NodeStatus.ok))
        runtime.register("b")(lambda ctx: NodeResult(node="b", status=NodeStatus.ok))
        run = runtime.run("go", approvals={"b"})
        assert run.status is NodeStatus.ok
        assert [s.node for s in run.steps] == ["a", "b"]

    def test_raising_the_ceiling_releases_it_too(self):
        runtime = AgentRuntime(_minimal_spec(), audit=memory_log(), autonomous_up_to=RiskTier.medium)
        runtime.register("a")(lambda ctx: NodeResult(node="a", status=NodeStatus.ok))
        runtime.register("b")(lambda ctx: NodeResult(node="b", status=NodeStatus.ok))
        assert runtime.run("go").status is NodeStatus.ok

    def test_the_shipped_graph_gates_everything_that_produces_output(self, agent_spec):
        """No node is HIGH, because no node applies anything. Every node that
        produces a durable artefact is at least MEDIUM, so nothing that a
        reviewer would want to see slips through ungated."""
        producing = {"iac-builder", "release-executor"}
        for name in producing:
            assert agent_spec.nodes[name].gate_tier is RiskTier.medium, name
        assert not [
            n for n, node in agent_spec.nodes.items()
            if node.gate_tier is RiskTier.high
        ]


class TestFailureHandling:
    def test_a_failing_node_is_retried_then_escalates(self):
        spec = _minimal_spec(failure_policy={"default": {"retry": 2, "fallback": "escalate_to_human"}})
        runtime = AgentRuntime(spec, audit=memory_log())
        attempts = []

        @runtime.register("a")
        def _a(ctx):
            attempts.append(ctx.error_chain)
            raise RuntimeError("boom")

        runtime.register("b")(lambda ctx: NodeResult(node="b", status=NodeStatus.ok))
        run = runtime.run("go", approvals={"b"})
        assert run.status is NodeStatus.failed
        assert len(attempts) == 3, "one attempt plus two retries"
        assert run.escalation == "escalate_to_human"

    def test_a_retry_decontaminates_scratch_state(self):
        """Retrying with the context that caused the failure is how three
        retries fail identically."""
        spec = _minimal_spec(failure_policy={"default": {"retry": 1}})
        runtime = AgentRuntime(spec, audit=memory_log())
        seen: list[dict] = []

        @runtime.register("a")
        def _a(ctx: AgentContext):
            seen.append(dict(ctx.scratch))
            ctx.scratch["stale"] = "value"
            raise RuntimeError("boom")

        runtime.register("b")(lambda ctx: NodeResult(node="b", status=NodeStatus.ok))
        runtime.run("go", approvals={"b"})
        assert seen == [{}, {}], "the second attempt must not see the first attempt's scratch"

    def test_the_error_chain_is_bounded(self):
        spec = _minimal_spec(
            failure_policy={"default": {"retry": 99}},
            error_handling={"max_error_chain": 2},
        )
        runtime = AgentRuntime(spec, audit=memory_log())
        calls = []

        @runtime.register("a")
        def _a(ctx):
            calls.append(1)
            raise RuntimeError("boom")

        runtime.register("b")(lambda ctx: NodeResult(node="b", status=NodeStatus.ok))
        runtime.run("go", approvals={"b"})
        assert len(calls) <= 4, "max_error_chain must stop an unbounded retry loop"


class TestBudgets:
    def test_a_node_over_its_hard_ceiling_stops_the_run(self):
        spec = _minimal_spec(evals={"per_node_warn": 1, "per_node_hard": 10})
        runtime = AgentRuntime(spec, audit=memory_log())
        runtime.register("a")(lambda ctx: NodeResult(node="a", status=NodeStatus.ok, tokens=500))
        runtime.register("b")(lambda ctx: NodeResult(node="b", status=NodeStatus.ok))
        run = runtime.run("go", approvals={"b"})
        assert run.status is NodeStatus.failed
        assert "hard ceiling" in run.escalation

    def test_the_run_ceiling_is_enforced_too(self):
        spec = _minimal_spec(evals={"per_node_hard": 1000, "per_run_hard": 150})
        runtime = AgentRuntime(spec, audit=memory_log())
        runtime.register("a")(lambda ctx: NodeResult(node="a", status=NodeStatus.ok, tokens=100))
        runtime.register("b")(lambda ctx: NodeResult(node="b", status=NodeStatus.ok, tokens=100))
        run = runtime.run("go", approvals={"b"})
        assert run.status is NodeStatus.failed
        assert "per-run ceiling" in run.escalation


class TestGovernance:
    def test_a_missing_ticket_blocks_before_any_node_runs(self):
        spec = _minimal_spec(governance={"ticket_required": True, "ticket_pattern": r"[A-Z]+-\d+"})
        runtime = AgentRuntime(spec, audit=memory_log())
        ran = []
        runtime.register("a")(lambda ctx: ran.append("a") or NodeResult(node="a", status=NodeStatus.ok))
        run = runtime.run("go", {})
        assert run.status is NodeStatus.blocked
        assert not ran, "governance must gate before work starts, not after"

    def test_a_matching_ticket_passes(self):
        spec = _minimal_spec(governance={"ticket_required": True, "ticket_pattern": r"[A-Z]+-\d+"})
        runtime = AgentRuntime(spec, audit=memory_log())
        runtime.register("a")(lambda ctx: NodeResult(node="a", status=NodeStatus.ok))
        runtime.register("b")(lambda ctx: NodeResult(node="b", status=NodeStatus.ok))
        assert runtime.run("go", {"ticket": "PLAT-42"}, approvals={"b"}).status is NodeStatus.ok


class TestWiring:
    def test_every_declared_node_has_a_handler(self, agent_spec, deps):
        """A declared node with no handler silently skips at run time, which
        reads as 'the agent chose not to' rather than 'nobody wrote it'."""
        assert build_runtime(agent_spec, deps).unhandled() == []

    def test_a_full_plan_run_visits_the_pipeline_once(self, agent_spec, deps):
        run = build_runtime(agent_spec, deps).run(
            "plan",
            {"graph": "reference-web-service", "environment": "dev", "languages": ["terraform"]},
            approvals={"iac-builder", "knowledge-curator"},
        )
        assert run.status is NodeStatus.ok
        assert [s.node for s in run.steps] == [
            "orchestrator", "iac-builder", "iac-validator", "sre-observer", "knowledge-curator",
        ]

    def test_a_request_carries_a_trace_id(self, agent_spec, deps):
        run = build_runtime(agent_spec, deps).run(
            "plan",
            {"graph": "reference-web-service", "environment": "dev"},
            approvals={"iac-builder", "knowledge-curator"},
        )
        assert run.request_id
        if run.trace_id:
            assert run.request_id == run.trace_id, "the request id is the trace id when tracing is on"

    def test_every_node_execution_is_audited(self, agent_spec, deps):
        build_runtime(agent_spec, deps).run(
            "plan",
            {"graph": "reference-web-service", "environment": "dev"},
            approvals={"iac-builder", "knowledge-curator"},
        )
        stages = [e["stage"] for e in deps.audit.events]
        assert stages[0] == "request_start" and stages[-1] == "request_end"
        assert stages.count("agent_node") >= 4


class TestRepairLoop:
    @pytest.fixture
    def broken_graph(self, repo):
        doc = yaml.safe_load((repo / "graphs" / "reference-web-service.graph.yaml").read_text())
        for node in doc["spec"]["nodes"]:
            if node["id"] == "api":
                node["params"]["desired_count"] = 1
            if node["id"] == "db":
                node["params"]["multi_az"] = False
        return doc

    def test_the_validator_hands_work_back_and_the_run_converges(
        self, agent_spec, deps, broken_graph
    ):
        run = build_runtime(agent_spec, deps).run(
            "plan",
            {"graph_document": broken_graph, "environment": "prod", "languages": ["terraform"]},
            approvals={"iac-builder", "knowledge-curator"},
        )
        assert run.status is NodeStatus.ok
        assert [s.node for s in run.steps].count("iac-builder") == 2
        assert any(s.needs_repair for s in run.steps)
        assert {r["node_id"] for r in run.output("repairs")} == {"api", "db"}

    def test_downstream_nodes_do_not_run_on_a_rejected_plan(
        self, agent_spec, deps, broken_graph
    ):
        run = build_runtime(agent_spec, deps).run(
            "plan",
            {"graph_document": broken_graph, "environment": "prod", "languages": ["terraform"]},
            approvals={"iac-builder", "knowledge-curator"},
        )
        assert [s.node for s in run.steps].count("sre-observer") == 1

    def test_an_unrepairable_block_stops_the_run(self, agent_spec, deps, broken_graph):
        doc = copy.deepcopy(broken_graph)
        for node in doc["spec"]["nodes"]:
            if node["id"] == "artifacts":
                node["exposure"] = "public"
        run = build_runtime(agent_spec, deps).run(
            "plan",
            {"graph_document": doc, "environment": "dev", "languages": ["terraform"]},
            approvals={"iac-builder"},
        )
        assert run.status is NodeStatus.blocked
        assert "NET-001" in run.steps[-1].summary

    def test_the_cycle_limit_is_enforced(self):
        """A repair loop that never converges must escalate, not thrash."""
        spec = AgentGraphSpec.model_validate(
            {
                "entry": "builder",
                "nodes": {
                    "builder": {"role": "b", "zone": ["build"]},
                    "validator": {"role": "v", "zone": ["check"]},
                },
                "edges": [
                    {"from": "builder", "to": "validator", "trigger": "always"},
                    {"from": "validator", "to": "builder", "trigger": "repair", "max_cycles": 2},
                ],
            }
        )
        runtime = AgentRuntime(spec, audit=memory_log())
        runtime.register("builder")(lambda ctx: NodeResult(node="builder", status=NodeStatus.ok))
        runtime.register("validator")(
            lambda ctx: NodeResult(
                node="validator",
                status=NodeStatus.ok,
                needs_repair=True,
                repair_reason="never converges",
            )
        )
        run = runtime.run("go")
        assert run.status is NodeStatus.blocked
        assert "2-cycle repair limit" in run.escalation


def test_a_grounding_failure_blocks_rather_than_improvising(agent_spec, deps, repo):
    doc = yaml.safe_load((repo / "graphs" / "reference-web-service.graph.yaml").read_text())
    doc["spec"]["nodes"][0]["pattern"] = "a-pattern-that-does-not-exist"
    run = build_runtime(agent_spec, deps).run(
        "plan", {"graph_document": doc, "environment": "dev"}, approvals={"iac-builder"}
    )
    assert run.status is NodeStatus.blocked
    assert "not grounded" in run.steps[-1].summary
