"""Spec, naming, graph ordering and grounding."""

from __future__ import annotations

import pytest

from opencloudops.graph import GraphError, dependency_map, resolve, topological_order
from opencloudops.ground import GroundingError, PatternCatalog
from opencloudops.naming import NamingError, logical_id, resource_name, tf_identifier
from opencloudops.spec import (
    EnvironmentSpec,
    GraphMetadata,
    GraphNode,
    GraphSpecBody,
    NodeType,
    PlatformContract,
    Provider,
    ResourceGraph,
)


def _graph(nodes, edges=()):
    return ResourceGraph(
        metadata=GraphMetadata(
            name="t", service="tst", owner="o", cost_center="c", environments=["dev"]
        ),
        spec=GraphSpecBody(nodes=nodes, edges=list(edges)),
    )


class TestNaming:
    def test_template_renders_every_token(self, platform):
        name = resource_name(
            platform,
            environment="prod",
            service="billing",
            component="api",
            node_type=NodeType.container_service,
        )
        assert name == "fsa-plat-prd-billing-api-svc"

    def test_globally_unique_names_are_deterministic(self, platform):
        kwargs = dict(
            environment="dev",
            service="billing",
            component="files",
            node_type=NodeType.object_store,
            globally_unique=True,
        )
        assert resource_name(platform, **kwargs) == resource_name(platform, **kwargs)

    def test_truncation_does_not_collide(self, platform):
        """A blind trim would map both of these to the same string."""
        long_a = resource_name(
            platform, environment="dev", service="svc", component="a" * 60,
            node_type=NodeType.function, max_length=40,
        )
        long_b = resource_name(
            platform, environment="dev", service="svc", component="a" * 61,
            node_type=NodeType.function, max_length=40,
        )
        assert len(long_a) <= 40 and len(long_b) <= 40
        assert long_a != long_b

    def test_unknown_token_is_an_error_not_a_literal(self, platform):
        platform = platform.model_copy(deep=True)
        platform.naming.template = "{org}-{env}-{service}-{nope}"
        with pytest.raises(NamingError, match="unknown token"):
            resource_name(
                platform, environment="dev", service="s", component="c",
                node_type=NodeType.function,
            )

    def test_identifier_helpers(self):
        assert logical_id("my-svc", "api") == "MySvcApi"
        assert tf_identifier("my-svc", "api") == "my_svc_api"
        assert tf_identifier("9lives")[0].isalpha()

    def test_environment_must_be_declared(self, platform):
        with pytest.raises(KeyError, match="not declared"):
            resource_name(
                platform, environment="nowhere", service="s", component="c",
                node_type=NodeType.function,
            )


class TestPlatformContract:
    def test_duplicate_environment_shorts_are_rejected(self):
        with pytest.raises(ValueError, match="short tokens must be unique"):
            PlatformContract(
                org="ab", business_unit="cd",
                environments=[
                    EnvironmentSpec(name="dev", short="dv", region="us-east-1"),
                    EnvironmentSpec(name="test", short="dv", region="us-east-1"),
                ],
            )

    def test_naming_template_must_carry_required_tokens(self, platform):
        with pytest.raises(ValueError, match="must include"):
            type(platform.naming)(template="{org}-{component}")


class TestGraphOrdering:
    def test_topological_order_is_deterministic(self, reference_graph):
        assert topological_order(reference_graph) == topological_order(reference_graph)

    def test_dependencies_precede_dependents(self, reference_graph):
        order = topological_order(reference_graph)
        deps = dependency_map(reference_graph)
        for node, required in deps.items():
            for r in required:
                assert order.index(r) < order.index(node), f"{r} must precede {node}"

    def test_cycle_is_refused_with_the_members_named(self):
        graph = _graph(
            [
                GraphNode(id="a", type=NodeType.function),
                GraphNode(id="b", type=NodeType.function),
            ],
            [
                {"from": "a", "to": "b", "relation": "depends_on"},
                {"from": "b", "to": "a", "relation": "depends_on"},
            ],
        )
        with pytest.raises(GraphError, match="cycle"):
            topological_order(graph)

    def test_data_flow_relations_do_not_constrain_order(self):
        """publishes/consumes describe data, not creation order."""
        graph = _graph(
            [
                GraphNode(id="a", type=NodeType.function),
                GraphNode(id="q", type=NodeType.queue),
            ],
            [{"from": "a", "to": "q", "relation": "publishes"}],
        )
        assert dependency_map(graph)["a"] == []

    def test_edge_to_unknown_node_is_refused(self):
        with pytest.raises(ValueError, match="unknown node"):
            _graph(
                [GraphNode(id="a", type=NodeType.function)],
                [{"from": "a", "to": "ghost", "relation": "depends_on"}],
            )


class TestGrounding:
    def test_every_node_type_has_an_approved_pattern(self, catalog):
        """The catalog is the only thing agents may build. A node type with no
        pattern is a node type the architect cannot use, so this must hold."""
        for node_type in NodeType:
            assert catalog.candidates(node_type, Provider.aws), f"no pattern for {node_type.value}"

    def test_missing_pattern_fails_closed(self, catalog):
        with pytest.raises(GroundingError, match="no pattern"):
            catalog.resolve(NodeType.function, Provider.gcp)

    def test_unknown_parameter_is_refused_not_passed_through(self, catalog):
        pattern = catalog.get("aws-lambda-function")
        with pytest.raises(GroundingError, match="no parameter"):
            catalog.merge_params(pattern, {"memory_mb": 512, "sneaky_flag": True})

    def test_out_of_range_parameter_is_refused(self, catalog):
        pattern = catalog.get("aws-ecs-fargate-service")
        with pytest.raises(GroundingError, match="not allowed"):
            catalog.merge_params(pattern, {"cpu": 999})

    def test_required_parameter_must_be_supplied(self, catalog):
        pattern = catalog.get("aws-dynamodb-table")
        with pytest.raises(GroundingError, match="requires parameter"):
            catalog.merge_params(pattern, {})

    def test_defaults_are_applied(self, catalog):
        merged = catalog.merge_params(catalog.get("aws-lambda-function"), {})
        assert merged["runtime"] == "python3.12"


class TestResolution:
    def test_every_node_is_named_tagged_and_grounded(self, dev_plan, platform):
        for node in dev_plan.nodes:
            assert node.name and node.pattern_id
            for tag in platform.tagging.required:
                assert node.tags.get(tag), f"{node.id} missing tag {tag}"

    def test_undeclared_environment_is_refused(self, reference_graph, platform, catalog):
        with pytest.raises(GraphError, match="does not declare environment"):
            resolve(reference_graph, platform, catalog, "staging-x")

    def test_production_flag_comes_from_the_contract(self, prod_plan, dev_plan):
        assert prod_plan.production is True
        assert dev_plan.production is False
