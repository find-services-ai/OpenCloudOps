"""The ChatOps command plane and the HTTP surface."""

from __future__ import annotations

import pytest

from opencloudops.chatops import ChatOps, parse
from opencloudops.chatops.commands import CommandError


class TestParser:
    def test_verb_target_and_options(self):
        cmd = parse("plan reference-web-service --env prod --ticket PLAT-1")
        assert cmd.verb == "plan"
        assert cmd.target == "reference-web-service"
        assert cmd.options == {"env": "prod", "ticket": "PLAT-1"}

    def test_slash_and_occ_prefixes_are_accepted(self):
        assert parse("/occ plan x").verb == "plan"
        assert parse("occ plan x").verb == "plan"

    def test_quoted_values_survive(self):
        cmd = parse('diagnose api --signal "exit code 137 OOMKilled"')
        assert cmd.options["signal"] == "exit code 137 OOMKilled"

    def test_equals_form_works(self):
        assert parse("plan x --env=prod").options["env"] == "prod"

    def test_a_bare_flag_is_true(self):
        assert parse("iac x --show").options["show"] == "true"

    def test_an_unbalanced_quote_is_an_error_not_a_guess(self):
        with pytest.raises(CommandError, match="unbalanced"):
            parse('diagnose api --signal "unterminated')

    def test_a_stray_positional_is_refused(self):
        with pytest.raises(CommandError, match="unexpected argument"):
            parse("plan a b")

    def test_empty_input_is_refused(self):
        with pytest.raises(CommandError, match="empty command"):
            parse("   ")


@pytest.fixture
def chat(deps, agent_spec, tmp_path):
    return ChatOps(deps, agent_spec, out_dir=tmp_path)


class TestApprovals:
    def test_a_gated_command_is_held_and_says_how_to_proceed(self, chat):
        reply = chat.handle("plan reference-web-service")
        assert reply.status == "awaiting-approval"
        assert "approve iac-builder" in reply.text

    def test_approving_then_re_running_works(self, chat):
        assert chat.handle("approve iac-builder").status == "ok"
        assert chat.handle("plan reference-web-service").status == "ok"

    def test_a_high_gate_is_refused_from_chat(self, deps, tmp_path):
        """HIGH is reserved for an apply capability this framework does not
        ship. The refusal is tested against a graph that declares one, so the
        rule stays proven even though no shipped node is HIGH."""
        from opencloudops.agentgraph import AgentGraphSpec

        spec = AgentGraphSpec.model_validate(
            {
                "entry": "orchestrator",
                "nodes": {
                    "orchestrator": {"role": "dispatch", "zone": ["routing"]},
                    "applier": {"role": "applies changes", "zone": ["apply"], "risk_gate": "HIGH"},
                },
                "edges": [{"from": "orchestrator", "to": "applier", "trigger": "apply"}],
            }
        )
        session = ChatOps(deps, spec, out_dir=tmp_path)
        reply = session.handle("approve applier")
        assert reply.status == "refused"
        assert "applier" not in session.approvals

    def test_no_shipped_node_is_high(self, agent_spec):
        """If this ever fails, something gained an apply capability -- which is
        a decision to make deliberately, not to discover in review."""
        high = [n for n, node in agent_spec.nodes.items()
                if node.gate_tier and node.gate_tier.value == "HIGH"]
        assert high == [], f"HIGH-gated nodes now exist: {high}"

    def test_approvals_do_not_leak_between_sessions(self, deps, agent_spec, tmp_path):
        first = ChatOps(deps, agent_spec, out_dir=tmp_path)
        first.handle("approve iac-builder")
        second = ChatOps(deps, agent_spec, out_dir=tmp_path)
        assert second.approvals == set()


class TestCommands:
    def test_unknown_verb_is_rejected_with_help(self, chat):
        reply = chat.handle("deploy everything")
        assert reply.status == "error" and "help" in reply.text

    def test_listings_need_no_approval(self, chat):
        for command in ("graphs", "patterns", "backends", "lessons", "questions", "help"):
            assert chat.handle(command).status == "ok", command

    def test_diagnose_requires_a_signal(self, chat):
        assert chat.handle("diagnose refsvc").status == "error"

    def test_diagnose_reports_the_pattern(self, chat):
        reply = chat.handle('diagnose refsvc --signal "OOMKilled exit code 137"')
        assert "oom-kill" in reply.text

    def test_remediate_states_whether_a_human_is_needed(self, chat):
        reply = chat.handle('remediate refsvc --signal "exit code 137" --env prod')
        assert "needs a human" in reply.text

    def test_plan_returns_a_diagram(self, chat):
        chat.handle("approve iac-builder")
        assert "```mermaid" in chat.handle("plan reference-web-service").text

    def test_slo_renders_a_table(self, chat):
        chat.handle("approve iac-builder")
        reply = chat.handle("slo reference-web-service")
        assert "error budget" in reply.text and "| `api` |" in reply.text

    def test_status_reflects_the_last_run(self, chat):
        chat.handle("approve iac-builder")
        chat.handle("plan reference-web-service")
        assert "Last run" in chat.handle("status").text

    def test_write_refuses_outside_the_output_directory(self, chat):
        chat.handle("approve iac-builder")
        reply = chat.handle("iac reference-web-service --write ../../etc")
        assert "only target a path inside" in reply.text

    def test_write_needs_the_builder_approval(self, deps, agent_spec, tmp_path):
        """--write is the one command that touches disk, so it is the one that
        actually needs the MEDIUM gate rather than the dry-run allowance."""
        session = ChatOps(deps, agent_spec, out_dir=tmp_path)
        session.approvals.clear()
        reply = session.handle("iac reference-web-service --write build")
        assert reply.status == "awaiting-approval"


class TestApi:
    @pytest.fixture
    def client(self, monkeypatch, repo):
        monkeypatch.setenv("OCC_PLATFORM", str(repo / "platform" / "platform.yaml"))
        monkeypatch.setenv("OCC_PATTERNS", str(repo / "patterns"))
        monkeypatch.setenv("OCC_GRAPHS", str(repo / "graphs"))
        monkeypatch.setenv("OCC_AGENT_GRAPH", str(repo / "agent-graph.yaml"))
        from fastapi.testclient import TestClient

        import opencloudops.api as api_module

        return TestClient(api_module.app)

    def test_health_and_readiness(self, client):
        assert client.get("/health").json()["status"] == "ok"
        ready = client.get("/ready").json()
        assert ready["status"] == "ready" and ready["patterns"] >= 9

    def test_plan_returns_a_grounded_plan_and_a_guard_report(self, client):
        body = client.post("/plan", json={"graph": "reference-web-service", "environment": "prod"}).json()
        assert body["run"]["status"] == "ok"
        assert len(body["plan"]["nodes"]) == 8
        assert body["guard"]["checks_run"] >= 20

    def test_iac_returns_paths_by_default_and_content_on_request(self, client):
        lean = client.post(
            "/iac", json={"graph": "reference-web-service", "languages": ["terraform"]}
        ).json()
        assert isinstance(next(iter(lean["artifacts"]["terraform"].values())), int)
        full = client.post(
            "/iac",
            json={"graph": "reference-web-service", "languages": ["terraform"], "include_content": True},
        ).json()
        assert "terraform {" in full["artifacts"]["terraform"]["versions.tf"]

    def test_an_unknown_backend_is_a_422(self, client):
        r = client.post("/iac", json={"graph": "reference-web-service", "languages": ["ansible"]})
        assert r.status_code == 422

    def test_a_graph_that_does_not_exist_is_a_404(self, client):
        """A typo is a client error, not a 200 carrying a failed agent run."""
        assert client.post("/plan", json={"graph": "nope"}).status_code == 404

    def test_a_request_with_neither_graph_nor_document_is_a_422(self, client):
        assert client.post("/plan", json={"environment": "dev"}).status_code == 422

    def test_the_high_gate_cannot_be_granted_over_http(self, client, monkeypatch, repo, tmp_path):
        """Refused by rule, not by node name: a future HIGH node inherits the
        refusal without anyone remembering to add it here."""
        import yaml

        spec = yaml.safe_load((repo / "agent-graph.yaml").read_text())
        spec["nodes"]["applier"] = {
            "role": "applies changes", "zone": ["apply-execution"], "risk_gate": "HIGH",
        }
        path = tmp_path / "agent-graph.yaml"
        path.write_text(yaml.safe_dump(spec))
        monkeypatch.setenv("OCC_AGENT_GRAPH", str(path))

        import importlib

        import opencloudops.api as api_module

        importlib.reload(api_module)
        from fastapi.testclient import TestClient

        local = TestClient(api_module.app)
        r = local.post(
            "/plan", json={"graph": "reference-web-service", "approvals": ["applier"]}
        )
        assert r.status_code == 403
        importlib.reload(api_module)

    def test_sre_routes(self, client):
        d = client.post(
            "/sre/diagnose", json={"service": "refsvc", "signal": "OOMKilled", "environment": "prod"}
        ).json()
        assert d["diagnosis"]["pattern_id"] == "oom-kill"

        r = client.post(
            "/sre/remediate", json={"service": "refsvc", "signal": "OOMKilled", "environment": "prod"}
        ).json()
        assert r["applied"] is False
        assert r["proposal"]["autonomous"] is False

        o = client.post("/sre/observe", json={"graph": "reference-web-service"}).json()
        assert o["slos"]["objectives"] and o["runbook"]

    def test_there_is_no_apply_endpoint(self, client):
        """Applying is the HIGH-gated node. Adding a route for it would need
        that gate moved, not merely a handler written."""
        paths = client.get("/openapi.json").json()["paths"]
        assert not any("apply" in p for p in paths)

    def test_promote_never_reports_itself_as_applied(self, client):
        body = client.post(
            "/promote", json={"graph": "reference-web-service", "environment": "staging"}
        ).json()
        assert body["promotion"]["applied"] is False

    def test_chat_route_returns_markdown(self, client):
        body = client.post("/chat", json={"text": "backends"}).json()
        assert "cdk-typescript" in body["text"]

    def test_agent_graph_is_introspectable(self, client):
        body = client.get("/agent-graph").json()
        assert set(body["nodes"]) == {
            "orchestrator", "iac-builder", "iac-validator",
            "sre-observer", "knowledge-curator", "release-executor",
        }
        assert body["nodes"]["release-executor"]["risk_gate"] == "MEDIUM"

    def test_every_response_carries_a_request_id(self, client):
        body = client.post("/plan", json={"graph": "reference-web-service"}).json()
        assert body["run"]["request_id"]
