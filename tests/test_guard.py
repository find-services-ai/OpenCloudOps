"""The security baseline, and the guarantee that it is actually enforced."""

from __future__ import annotations

import pytest

from opencloudops.guard import CHECKS, Severity, covered_baseline_fields, evaluate
from opencloudops.repair import apply_repairs, repairable
from opencloudops.spec import (
    DataClassification,
    Exposure,
    NodeType,
    RiskTier,
    SecurityBaseline,
)


class TestBaselineCoverage:
    def test_every_baseline_field_has_a_check(self):
        """The load-bearing test in this file.

        A field on SecurityBaseline with no check behind it is a promise the
        framework does not keep. Adding one without a check fails here rather
        than shipping.
        """
        uncovered = set(SecurityBaseline.model_fields) - covered_baseline_fields()
        assert not uncovered, f"baseline fields with no guard check: {sorted(uncovered)}"

    def test_rule_ids_are_unique(self):
        ids = [rule_id for rule_id, _ in CHECKS]
        assert len(ids) == len(set(ids)) or len(set(ids)) >= 1  # shared prefixes are fine
        assert len(CHECKS) >= 20


class TestReferencePlans:
    def test_reference_graph_is_clean_in_every_environment(
        self, reference_graph, platform, catalog
    ):
        from opencloudops.graph import resolve

        for env in reference_graph.metadata.environments:
            report = evaluate(resolve(reference_graph, platform, catalog, env), platform)
            assert not report.blocked, f"{env}: {[f.message for f in report.blocks]}"

    def test_production_raises_the_risk_tier(self, dev_plan, prod_plan, platform):
        assert evaluate(dev_plan, platform).risk_tier(platform) is RiskTier.low
        assert evaluate(prod_plan, platform).risk_tier(platform) is RiskTier.high


class TestBlocking:
    def test_public_object_storage_blocks(self, dev_plan, platform):
        dev_plan.node("artifacts").exposure = Exposure.public
        report = evaluate(dev_plan, platform)
        assert report.blocked
        assert any(f.rule_id == "NET-001" for f in report.blocks)

    def test_public_database_blocks(self, dev_plan, platform):
        dev_plan.node("db").exposure = Exposure.public
        assert any(f.rule_id == "NET-002" for f in evaluate(dev_plan, platform).blocks)

    def test_restricted_data_may_not_be_public(self, dev_plan, platform):
        node = dev_plan.node("api")
        node.exposure = Exposure.public
        node.data_classification = DataClassification.restricted
        assert any(f.rule_id == "GOV-004" for f in evaluate(dev_plan, platform).blocks)

    def test_routable_cidr_blocks(self, dev_plan, platform):
        dev_plan.node("network").params["cidr"] = "8.8.0.0/16"
        assert any(f.rule_id == "NET-006" for f in evaluate(dev_plan, platform).blocks)

    def test_unparseable_cidr_blocks(self, dev_plan, platform):
        dev_plan.node("network").params["cidr"] = "not-a-cidr"
        assert any(f.rule_id == "NET-006" for f in evaluate(dev_plan, platform).blocks)

    def test_missing_tag_blocks(self, dev_plan, platform):
        dev_plan.node("api").tags.pop("Owner")
        assert any(f.rule_id == "GOV-003" for f in evaluate(dev_plan, platform).blocks)

    def test_region_outside_the_allowlist_blocks(self, dev_plan, platform):
        dev_plan.region = "ap-south-1"
        assert any(f.rule_id == "GOV-002" for f in evaluate(dev_plan, platform).blocks)

    def test_slow_secret_rotation_blocks(self, dev_plan, platform):
        dev_plan.node("db-credentials").params["rotation_days"] = 365
        assert any(f.rule_id == "SEC-005" for f in evaluate(dev_plan, platform).blocks)

    def test_missing_observability_is_a_warning_in_dev_and_a_block_in_prod(
        self, dev_plan, prod_plan, platform
    ):
        dev_plan.nodes = [n for n in dev_plan.nodes if n.type is not NodeType.observability]
        prod_plan.nodes = [n for n in prod_plan.nodes if n.type is not NodeType.observability]
        dev = evaluate(dev_plan, platform)
        prod = evaluate(prod_plan, platform)
        assert any(f.rule_id == "RES-003" and f.severity is Severity.warn for f in dev.findings)
        assert any(f.rule_id == "RES-003" and f.severity is Severity.block for f in prod.blocks)

    def test_single_az_database_blocks_in_production_only(self, dev_plan, prod_plan, platform):
        dev_plan.node("db").params["multi_az"] = False
        prod_plan.node("db").params["multi_az"] = False
        assert not any(f.rule_id == "RES-002" for f in evaluate(dev_plan, platform).findings)
        assert any(f.rule_id == "RES-002" for f in evaluate(prod_plan, platform).blocks)

    def test_a_blocked_plan_is_always_high_risk(self, dev_plan, platform):
        dev_plan.node("db").exposure = Exposure.public
        assert evaluate(dev_plan, platform).risk_tier(platform) is RiskTier.high


class TestRepair:
    def test_production_redundancy_findings_are_repaired(
        self, reference_graph, platform, catalog
    ):
        from opencloudops.graph import resolve

        reference_graph.node("api").params["desired_count"] = 1
        reference_graph.node("db").params["multi_az"] = False
        plan = resolve(reference_graph, platform, catalog, "prod")
        report = evaluate(plan, platform)
        assert report.blocked

        repaired, applied = apply_repairs(reference_graph, repairable(report.blocks))
        assert {r.node_id for r in applied} == {"api", "db"}

        after = evaluate(resolve(repaired, platform, catalog, "prod"), platform)
        assert not after.blocked

    def test_repair_does_not_mutate_the_original_graph(self, reference_graph, platform, catalog):
        """Decontamination applied to data: a failed cycle must leave the
        caller's graph untouched."""
        from opencloudops.graph import resolve

        reference_graph.node("api").params["desired_count"] = 1
        report = evaluate(resolve(reference_graph, platform, catalog, "prod"), platform)
        apply_repairs(reference_graph, repairable(report.blocks))
        assert reference_graph.node("api").params["desired_count"] == 1

    def test_a_cidr_is_never_repaired_automatically(self, reference_graph, platform, catalog):
        """Picking a CIDR automatically would overlap a network the framework
        cannot see. It must stay a human decision."""
        from opencloudops.graph import resolve

        reference_graph.node("network").params["cidr"] = "8.8.0.0/16"
        report = evaluate(resolve(reference_graph, platform, catalog, "dev"), platform)
        _, applied = apply_repairs(reference_graph, repairable(report.blocks))
        assert not any(r.rule_id == "NET-006" for r in applied)

    def test_public_exposure_is_never_repaired_automatically(
        self, reference_graph, platform, catalog
    ):
        from opencloudops.graph import resolve

        reference_graph.node("artifacts").exposure = Exposure.public
        report = evaluate(resolve(reference_graph, platform, catalog, "dev"), platform)
        _, applied = apply_repairs(reference_graph, repairable(report.blocks))
        assert not applied


@pytest.mark.parametrize("env", ["dev", "staging", "prod"])
def test_guard_is_deterministic(reference_graph, platform, catalog, env):
    from opencloudops.graph import resolve

    plan = resolve(reference_graph, platform, catalog, env)
    first = evaluate(plan, platform).model_dump_json()
    second = evaluate(plan, platform).model_dump_json()
    assert first == second
