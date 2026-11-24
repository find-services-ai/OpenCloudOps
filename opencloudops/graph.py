"""The resource graph: load, order, and resolve.

A ``ResourceGraph`` is provider-neutral and human-authored. A ``ResolvedPlan``
is what the rest of the framework actually consumes: every node grounded in a
pattern, named by the platform contract, tagged, and sorted into a dependency
order that is safe to apply.

Cycle detection is not a nicety here. An IaC backend given a cyclic graph
either loops forever or emits something that cannot converge, so a cycle is
refused at this boundary with the actual cycle printed.
"""

from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path

import yaml

from opencloudops.ground import GroundingError, PatternCatalog
from opencloudops.naming import resource_name
from opencloudops.spec import (
    DataClassification,
    NodeType,
    PlatformContract,
    Relation,
    ResolvedNode,
    ResolvedPlan,
    ResourceGraph,
)


class GraphError(Exception):
    """Raised when a graph is structurally unusable."""


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------


def load_graph(path: Path | str) -> ResourceGraph:
    path = Path(path)
    if not path.exists():
        raise GraphError(f"no graph file at {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise GraphError(f"{path}: expected a single YAML mapping, got {type(raw).__name__}")
    try:
        return ResourceGraph.model_validate(raw)
    except Exception as e:
        raise GraphError(f"{path}: {e}") from e


def load_platform(path: Path | str) -> PlatformContract:
    path = Path(path)
    if not path.exists():
        raise GraphError(f"no platform contract at {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise GraphError(f"{path}: expected a YAML mapping")
    # Accept both a bare contract and one nested under `platform:`.
    body = raw.get("platform", raw)
    try:
        return PlatformContract.model_validate(body)
    except Exception as e:
        raise GraphError(f"{path}: {e}") from e


# ---------------------------------------------------------------------------
# ordering
# ---------------------------------------------------------------------------

#: Relations that imply "the target must exist before the source is applied".
#: `publishes`/`consumes` are informational -- they describe data flow between
#: two things that may be created in either order -- so they do not constrain
#: apply order, but they are still carried into the plan for the SRE agent.
ORDERING_RELATIONS = {Relation.depends_on, Relation.reads, Relation.writes, Relation.invokes}


def dependency_map(graph: ResourceGraph) -> dict[str, list[str]]:
    """node id -> ids it must be applied after."""
    deps: dict[str, list[str]] = {n.id: [] for n in graph.spec.nodes}
    for e in graph.spec.edges:
        if e.relation in ORDERING_RELATIONS:
            deps[e.from_].append(e.to)
    return {k: sorted(set(v)) for k, v in deps.items()}


def topological_order(graph: ResourceGraph) -> list[str]:
    """Kahn's algorithm, ties broken alphabetically so output is reproducible.

    Reproducibility matters more than it looks: the emitters write files from
    this order, so a nondeterministic sort would produce a spurious diff on
    every run and make review worthless.
    """
    deps = dependency_map(graph)
    dependents: dict[str, set[str]] = defaultdict(set)
    indegree = {node_id: len(d) for node_id, d in deps.items()}
    for node_id, d in deps.items():
        for target in d:
            dependents[target].add(node_id)

    ready = deque(sorted(n for n, deg in indegree.items() if deg == 0))
    order: list[str] = []
    while ready:
        current = ready.popleft()
        order.append(current)
        newly: list[str] = []
        for dependent in sorted(dependents[current]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                newly.append(dependent)
        for n in newly:
            ready.append(n)
        ready = deque(sorted(ready))

    if len(order) != len(deps):
        stuck = sorted(set(deps) - set(order))
        raise GraphError(
            "dependency cycle in the resource graph involving: "
            + ", ".join(stuck)
            + ". Break it by removing one edge -- IaC cannot converge on a cycle."
        )
    return order


# ---------------------------------------------------------------------------
# resolution
# ---------------------------------------------------------------------------


_GLOBALLY_UNIQUE_TYPES = {NodeType.object_store}

#: Provider name-length ceilings that are stricter than the platform default.
_TYPE_MAX_LENGTH = {
    NodeType.object_store: 63,
    NodeType.relational: 60,
    NodeType.function: 64,
    NodeType.table: 255,
    NodeType.queue: 80,
}


def build_tags(
    platform: PlatformContract,
    graph: ResourceGraph,
    environment: str,
    node_classification: DataClassification,
    component: str,
) -> dict[str, str]:
    """Every mandatory tag, resolved. A tag the contract requires but that
    nothing can supply is an error here rather than a missing tag in prod."""
    env = platform.environment(environment)
    known = {
        "Org": platform.org,
        "BusinessUnit": platform.business_unit,
        "Environment": env.name,
        "Service": graph.metadata.service,
        "Component": component,
        "Owner": graph.metadata.owner,
        "CostCenter": graph.metadata.cost_center,
        "DataClassification": node_classification.value,
        "Application": graph.metadata.name,
        "Region": env.region,
    }
    tags: dict[str, str] = {}
    missing: list[str] = []
    for key in platform.tagging.required:
        if key in known:
            tags[key] = known[key]
        elif key in platform.tagging.defaults:
            tags[key] = platform.tagging.defaults[key]
        else:
            missing.append(key)
    if missing:
        raise GraphError(
            f"tagging.required lists {', '.join(missing)}, but neither the graph nor "
            "tagging.defaults supplies a value. Add a default to platform.yaml or drop the tag."
        )
    for key, value in platform.tagging.defaults.items():
        tags.setdefault(key, value)
    return tags


def resolve(
    graph: ResourceGraph,
    platform: PlatformContract,
    catalog: PatternCatalog,
    environment: str,
) -> ResolvedPlan:
    """Ground + name + tag every node in the graph for one environment."""
    if environment not in graph.metadata.environments:
        raise GraphError(
            f"graph {graph.metadata.name!r} does not declare environment {environment!r} "
            f"(declares: {', '.join(graph.metadata.environments)})"
        )
    env = platform.environment(environment)
    provider = platform.default_provider

    order = topological_order(graph)
    deps = dependency_map(graph)

    active = {
        n.id for n in graph.spec.nodes if n.environments is None or environment in n.environments
    }

    resolved: list[ResolvedNode] = []
    for node_id in order:
        if node_id not in active:
            continue
        node = graph.node(node_id)
        try:
            pattern = catalog.resolve(node.type, provider, explicit=node.pattern)
            params = catalog.merge_params(pattern, node.params)
        except GroundingError as e:
            raise GroundingError(f"node {node.id!r}: {e}") from e

        classification = node.data_classification or graph.metadata.data_classification
        name = resource_name(
            platform,
            environment=environment,
            service=graph.metadata.service,
            component=node.id,
            node_type=node.type,
            max_length=_TYPE_MAX_LENGTH.get(node.type),
            globally_unique=node.type in _GLOBALLY_UNIQUE_TYPES,
        )
        resolved.append(
            ResolvedNode(
                id=node.id,
                type=node.type,
                pattern_id=pattern.id,
                provider=pattern.provider,
                name=name,
                params=params,
                tags=build_tags(platform, graph, environment, classification, node.id),
                controls=list(pattern.controls),
                exposure=node.exposure,
                data_classification=classification,
                depends_on=[d for d in deps[node.id] if d in active],
                slo=pattern.slo,
            )
        )

    dangling = [
        e
        for e in graph.spec.edges
        if (e.from_ in active) != (e.to in active)
    ]
    if dangling:
        detail = ", ".join(f"{e.from_}->{e.to}" for e in dangling)
        raise GraphError(
            f"in environment {environment!r} these edges cross an inactive node: {detail}. "
            "A node excluded by `environments:` cannot be depended on by an active one."
        )

    return ResolvedPlan(
        graph_name=graph.metadata.name,
        service=graph.metadata.service,
        environment=environment,
        region=env.region,
        provider=provider,
        production=env.production,
        nodes=resolved,
        edges=[e for e in graph.spec.edges if e.from_ in active and e.to in active],
        order=[n.id for n in resolved],
    )


def to_mermaid(plan: ResolvedPlan) -> str:
    """Render the resolved plan as a Mermaid diagram -- what ChatOps posts
    back when someone asks what a plan actually builds."""
    lines = ["graph TD"]
    shape = {
        NodeType.network: ("([", "])"),
        NodeType.object_store: ("[(", ")]"),
        NodeType.table: ("[(", ")]"),
        NodeType.relational: ("[(", ")]"),
        NodeType.queue: (">", "]"),
    }
    for n in plan.nodes:
        open_b, close_b = shape.get(n.type, ("[", "]"))
        label = f"{n.id}<br/>{n.type.value}"
        lines.append(f"  {n.id.replace('-', '_')}{open_b}\"{label}\"{close_b}")
    for e in plan.edges:
        lines.append(
            f"  {e.from_.replace('-', '_')} -->|{e.relation.value}| {e.to.replace('-', '_')}"
        )
    return "\n".join(lines)
