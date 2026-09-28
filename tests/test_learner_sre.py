"""The learner's detectors, and the SRE plane's diagnosis and remediation."""

from __future__ import annotations

import pytest

from opencloudops.learner import DETECTORS, LESSONS, Learner, LessonSeverity, Memory
from opencloudops.sre import ERROR_PATTERNS, derive_slos, diagnose, propose, runbook
from opencloudops.sre.patterns import Confidence
from opencloudops.sre.slo import dashboard
from opencloudops.spec import RiskTier


class TestLessonRegistry:
    def test_every_lesson_has_a_detector(self):
        """The correction this module exists to make: a lesson nothing enforces
        is prose, and prose does not prevent anything."""
        assert set(LESSONS) == set(DETECTORS)
        assert len(LESSONS) >= 8


class TestDetectorsFire:
    """A detector that never fires is indistinguishable from no detector, so
    each one is proven against input it must reject."""

    def _scan(self, artifact, content, plan, platform):
        return Learner(memory_dir=None).scan({artifact: content}, plan, platform)

    def test_count_length_is_caught(self, dev_plan, platform):
        hits = self._scan("main.tf", "resource x {\n  count = length(var.services)\n}", dev_plan, platform)
        assert any(h.lesson_id == "for-each-over-count" for h in hits)

    def test_unpinned_module_is_caught(self, dev_plan, platform):
        content = 'module "vpc" {\n  source = "git::https://example.com/mod.git"\n}'
        hits = self._scan("main.tf", content, dev_plan, platform)
        assert any(h.lesson_id == "module-interface-validation" for h in hits)

    def test_a_pinned_module_is_not_flagged(self, dev_plan, platform):
        content = 'module "vpc" {\n  source = "git::https://example.com/mod.git?ref=v3.1.0"\n}'
        hits = self._scan("main.tf", content, dev_plan, platform)
        assert not any(h.lesson_id == "module-interface-validation" for h in hits)

    def test_inline_credentials_are_caught(self, dev_plan, platform):
        hits = self._scan("main.tf", 'password = "hunter2-not-a-variable"', dev_plan, platform)
        assert any(h.lesson_id == "secrets-not-inline" for h in hits)

    def test_a_variable_reference_is_not_a_credential(self, dev_plan, platform):
        hits = self._scan("main.tf", "password = var.db_password", dev_plan, platform)
        assert not any(h.lesson_id == "secrets-not-inline" for h in hits)

    def test_wildcard_iam_is_caught(self, dev_plan, platform):
        hits = self._scan("main.tf", '"Action" : "*"', dev_plan, platform)
        assert any(h.lesson_id == "no-wildcard-iam" for h in hits)

    def test_open_egress_is_caught_in_production_only(self, dev_plan, prod_plan, platform):
        content = 'cidr_blocks = ["0.0.0.0/0"]'
        assert not any(
            h.lesson_id == "no-open-egress-in-prod"
            for h in self._scan("main.tf", content, dev_plan, platform)
        )
        assert any(
            h.lesson_id == "no-open-egress-in-prod"
            for h in self._scan("main.tf", content, prod_plan, platform)
        )

    def test_another_environment_in_the_artifact_is_caught(self, dev_plan, platform):
        hits = self._scan("main.tf", 'endpoint = "fsa-plat-prd-refsvc-db-rds.example"', dev_plan, platform)
        assert any(h.lesson_id == "env-leak" for h in hits)

    def test_a_substring_is_not_an_environment_leak(self, dev_plan, platform):
        """'prd' inside 'prdct-catalog' is not a production reference."""
        hits = self._scan("main.tf", 'name = "prdct-catalog"', dev_plan, platform)
        assert not any(h.lesson_id == "env-leak" for h in hits)

    def test_comment_art_is_caught(self, dev_plan, platform):
        hits = self._scan("main.tf", "# " + "=" * 40, dev_plan, platform)
        assert any(h.lesson_id == "decorative-comments" for h in hits)

    def test_a_broken_detector_degrades_instead_of_failing_the_build(
        self, dev_plan, platform, monkeypatch
    ):
        def explode(*_args, **_kwargs):
            raise RuntimeError("detector bug")

        monkeypatch.setitem(DETECTORS, "env-leak", explode)
        hits = Learner(memory_dir=None).scan({"main.tf": "x"}, dev_plan, platform)
        assert any("detector error" in h.message for h in hits)


class TestMemory:
    def test_a_recorded_incident_is_recalled(self, tmp_path):
        learner = Learner(memory_dir=tmp_path)
        learner.record(
            Memory(
                service="refsvc", environment="prod", symptom="OOM",
                root_cause="memory ceiling too low", guard="raised to 1024MB",
            )
        )
        recalled = learner.recall(service="refsvc")
        assert len(recalled) == 1 and recalled[0].guard == "raised to 1024MB"

    def test_recall_filters_by_service(self, tmp_path):
        learner = Learner(memory_dir=tmp_path)
        for service in ("a-svc", "b-svc"):
            learner.record(
                Memory(service=service, environment="dev", symptom="s", root_cause="c", guard="g")
            )
        assert len(learner.recall(service="a-svc")) == 1
        assert len(learner.recall()) == 2

    def test_recall_is_empty_rather_than_failing_with_no_store(self, tmp_path):
        assert Learner(memory_dir=tmp_path / "nothing-here").recall() == []


class TestErrorPatterns:
    def test_patterns_are_well_formed(self):
        ids = [p.id for p in ERROR_PATTERNS]
        assert len(ids) == len(set(ids))
        for pattern in ERROR_PATTERNS:
            assert pattern.indicators and pattern.recommended_fix
            if not pattern.auto_remediable and pattern.risk_tier is RiskTier.high:
                assert pattern.never_retry_reason, f"{pattern.id} must say why it is never retried"

    @pytest.mark.parametrize(
        "signal,expected",
        [
            ("Container killed: OOMKilled, exit code 137", "oom-kill"),
            ("CannotPullContainerError: manifest not found", "image-pull-failure"),
            ("ThrottlingException: Rate exceeded", "throttling"),
            ("x509: certificate has expired", "tls-failure"),
            ("readiness probe failed", "health-check-failure"),
            ("No space left on device", "disk-pressure"),
            ("AccessDenied when calling the operation", "auth-failure"),
        ],
    )
    def test_known_signals_classify(self, signal, expected):
        d = diagnose(signal, service="s", environment="dev")
        assert d.matched and d.pattern_id == expected

    def test_an_unknown_signal_is_reported_as_unknown(self):
        """A confident wrong answer during an incident costs more than an
        honest 'unrecognised'."""
        d = diagnose("the vibes are off", service="s", environment="dev")
        assert not d.matched
        assert d.confidence is Confidence.low
        assert d.risk_tier is RiskTier.high
        assert d.next_steps

    def test_affected_nodes_are_resolved_from_the_plan(self, dev_plan):
        d = diagnose(
            "fsa-plat-dv-refsvc-api-svc reported exit code 137",
            service="refsvc", environment="dev", plan=dev_plan,
        )
        assert "api" in d.affected_nodes


class TestRemediation:
    def test_a_safe_low_risk_fix_is_autonomous_outside_production(self, platform):
        d = diagnose("exit code 137 OOMKilled", service="s", environment="dev")
        assert propose(d, platform).autonomous is True

    def test_production_is_excluded_unconditionally(self, platform):
        """The ladder usually excludes production anyway; relying on that would
        make a one-line config edit enough to hand an agent production."""
        d = diagnose("exit code 137 OOMKilled", service="s", environment="prod")
        proposal = propose(d, platform)
        assert proposal.autonomous is False
        assert "production is excluded" in proposal.blocked_reason

    def test_a_never_retry_pattern_is_never_autonomous(self, platform):
        d = diagnose("FATAL: the database system is starting up", service="s", environment="dev")
        proposal = propose(d, platform)
        assert proposal.autonomous is False
        assert "thundering herd" in proposal.blocked_reason

    def test_an_unknown_signal_escalates(self, platform):
        d = diagnose("nothing matches this", service="s", environment="dev")
        proposal = propose(d, platform)
        assert proposal.approval_required and proposal.blocked_reason == "unrecognised signal"

    def test_a_proposal_always_carries_a_rollback(self, platform):
        d = diagnose("Rate exceeded", service="s", environment="dev")
        assert propose(d, platform).rollback


class TestSLOs:
    def test_objectives_come_from_the_patterns(self, dev_plan):
        slos = derive_slos(dev_plan)
        assert {o.node_id for o in slos.objectives} == {"api", "worker", "db", "jobs", "artifacts"}

    def test_infrastructure_without_a_user_facing_signal_gets_no_objective(self, dev_plan):
        assert "network" not in {o.node_id for o in derive_slos(dev_plan).objectives}

    def test_error_budget_matches_the_objective(self, dev_plan):
        api = next(o for o in derive_slos(dev_plan).objectives if o.node_id == "api")
        assert api.objective_pct == 99.9
        assert api.error_budget_minutes == pytest.approx(43.2, rel=1e-3)

    def test_each_objective_gets_a_page_and_a_ticket_alert(self, dev_plan):
        for objective in derive_slos(dev_plan).objectives:
            severities = {a.severity for a in objective.alerts}
            assert severities == {"page", "ticket"}

    def test_the_runbook_names_real_resources_and_the_apply_order(self, dev_plan):
        slos = derive_slos(dev_plan)
        book = runbook(dev_plan, slos)
        for node in dev_plan.nodes:
            assert node.name in book
        assert " -> ".join(dev_plan.order) in book
        assert "never auto-remediated" in book

    def test_the_dashboard_describes_the_dependency_graph(self, dev_plan):
        board = dashboard(dev_plan, derive_slos(dev_plan))
        assert any(w["type"] == "graph" for w in board["widgets"])
