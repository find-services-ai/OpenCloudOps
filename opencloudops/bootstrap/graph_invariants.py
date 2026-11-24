"""Safety invariants for ``agent-graph.yaml``, beyond what Pydantic checks.

Ported from ``junction/graph_schema.py``. That module validates a raw graph
dict against a JSON Schema plus a fixed list of invariants a schema cannot
express (a read-only node has no write tool, a HIGH-consequence node never
auto-retries, a repair loop is actually bounded, ...).

``AgentGraphSpec`` already enforces graph *structure* through Pydantic
(zone disjointness, edges pointing at real nodes, a parseable risk gate --
see ``opencloudops/agentgraph.py``). What it does not check is *safety
policy over that structure* -- and that gap is exactly what this module
closes, adapted to OpenCloudOps's own six nodes and its "no HIGH gate
ships, because nothing here applies anything" design (see
``opencloudops/agent-graph.yaml``'s note on ``release-executor``).

Called from ``occ validate-graph`` and from the test suite; not from
``AgentGraphSpec.load()`` itself, so a load failure (bad YAML, unknown
field) and a policy failure (a read-only node gained a write tool) report
through two different, clearly-named paths rather than one crash.
"""

from __future__ import annotations

from opencloudops.agentgraph import AgentGraphSpec

#: Tools that make a node capable of changing a file or emitting code.
#: A node with none of these is read-only by construction, not by promise.
_WRITE_CAPABLE_TOOLS = {"edit", "emit-iac"}

#: Nodes this framework's own design declares read-only. Checked against the
#: graph's actual tool lists below, so a future edit that quietly adds
#: ``edit`` to one of these is caught here rather than discovered in review.
_EXPECTED_READ_ONLY = ("orchestrator", "iac-validator", "sre-observer")


def graph_invariant_errors(spec: AgentGraphSpec) -> list[str]:
    """Every safety-invariant violation in ``spec`` (empty list = valid)."""
    errors: list[str] = []
    nodes = spec.nodes
    edges = spec.edges

    # 1. The nodes this design calls read-only must have no write-capable tool.
    for name in _EXPECTED_READ_ONLY:
        node = nodes.get(name)
        if node is None:
            errors.append(f"nodes: expected a read-only node named {name!r}, not found")
            continue
        offending = [t for t in node.tools if t in _WRITE_CAPABLE_TOOLS]
        if offending:
            errors.append(
                f"nodes/{name}: declared read-only by design but has write-capable tool(s) "
                f"{offending} -- either remove the tool or move the node out of "
                "_EXPECTED_READ_ONLY if this is deliberate"
            )

    # 2. Nothing ships HIGH. This framework's entire "propose, never apply"
    #    guarantee rests on that -- see agent-graph.yaml's note on why
    #    release-executor is MEDIUM, not HIGH.
    high_nodes = [name for name, node in nodes.items() if node.risk_gate.upper() == "HIGH"]
    if high_nodes:
        errors.append(
            f"nodes: {high_nodes} are gated HIGH, but no node in this framework may apply "
            "anything -- HIGH is reserved for a capability that does not exist here. If one "
            "was just added, the ChatOps and API refusals that block granting HIGH (see "
            "opencloudops/chatops/commands.py and opencloudops/api.py) must be revisited first."
        )

    # 3. Any node whose failure_policy allows a retry on an edge this framework
    #    has marked irreversible must not silently thrash. Concretely: a node
    #    gated MEDIUM or above that writes a durable artefact should have a
    #    bounded, explicit retry count, not the default's implicit one.
    for name in ("iac-builder", "release-executor"):
        node = nodes.get(name)
        if node is None:
            continue
        policy = spec.failure_policy.per_node.get(name)
        if policy is None:
            errors.append(
                f"failure_policy/per_node: {name!r} is gated {node.risk_gate} and should declare "
                "an explicit retry policy rather than inheriting the default silently"
            )

    # 4. The builder -> validator "must pass" gate must exist: the builder must
    #    never be able to ship something the validator hasn't seen.
    if not any(e.from_ == "iac-builder" and e.to == "iac-validator" and e.gate == "must-pass" for e in edges):
        errors.append("edges: iac-builder -> iac-validator 'must-pass' gate is required")

    # 5. Any repair loop back to the builder must be bounded.
    for e in edges:
        if e.from_ == "iac-validator" and e.to == "iac-builder" and e.max_cycles is None:
            errors.append("edges: iac-validator -> iac-builder repair loop needs max_cycles")

    # 6. The orchestrator must have a route (direct or via another node) to
    #    every node -- an unreachable node is dead weight nobody can invoke.
    reachable: set[str] = set()
    frontier = [spec.entry]
    while frontier:
        current = frontier.pop()
        for e in edges:
            if e.from_ == current and e.to not in reachable and e.to != current:
                reachable.add(e.to)
                frontier.append(e.to)
    for name in nodes:
        if name != spec.entry and name not in reachable:
            errors.append(f"edges: {spec.entry!r} has no route to {name!r}")

    # 7. Token budgets must be ordered: a hard ceiling below the warn
    #    threshold, or a per-run ceiling below the per-node one, can never
    #    actually fire as designed.
    evals = spec.evals
    if not evals.per_node_warn < evals.per_node_hard <= evals.per_run_hard:
        errors.append(
            "evals: expected per_node_warn < per_node_hard <= per_run_hard, got "
            f"{evals.per_node_warn} / {evals.per_node_hard} / {evals.per_run_hard}"
        )

    return errors


class GraphInvariantError(ValueError):
    """Raised by :func:`validate_graph_invariants` when any invariant fails."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("agent graph fails safety invariants:\n  - " + "\n  - ".join(errors))


def validate_graph_invariants(spec: AgentGraphSpec) -> None:
    errors = graph_invariant_errors(spec)
    if errors:
        raise GraphInvariantError(errors)
