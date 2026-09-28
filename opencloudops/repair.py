"""Deterministic repairs for blocking guard findings.

This is what makes the validator -> builder edge in ``agent-graph.yaml`` a real
loop rather than a diagram. When the guard blocks a plan, some of those blocks
have exactly one correct answer: a production database that is single-AZ should
be Multi-AZ; a secret rotating slower than the baseline should rotate at the
baseline. There is no judgement in those, so an agent may make them and say so.

Everything else is deliberately *not* repairable here. A finding with more than
one defensible answer goes to a human, because a framework that guesses on
those trains people to stop reading its output.

Repairs mutate the *graph*, never the emitted files. Patching generated text
would be undone by the next run and would leave the graph still wrong.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from opencloudops.guard import Finding
from opencloudops.spec import NodeType, ResourceGraph


class Repair(BaseModel):
    rule_id: str
    node_id: str
    description: str
    before: str
    after: str


#: rule_id -> repair function. A function returns a Repair when it changed the
#: graph, or None when this particular finding is not one it can fix.
_REPAIRS: dict[str, Callable[[ResourceGraph, Finding], Repair | None]] = {}


def repair_for(rule_id: str) -> Callable[..., Callable[..., Repair | None]]:
    def wrap(fn: Callable[[ResourceGraph, Finding], Repair | None]) -> Callable[..., Repair | None]:
        _REPAIRS[rule_id] = fn
        return fn

    return wrap


@repair_for("RES-002")
def _production_redundancy(graph: ResourceGraph, finding: Finding) -> Repair | None:
    """Single points of failure in production each have one right answer."""
    if not finding.node_id:
        return None
    node = graph.node(finding.node_id)

    if node.params.get("single_nat_gateway") is True:
        node.params["single_nat_gateway"] = False
        return Repair(
            rule_id="RES-002",
            node_id=node.id,
            description="One NAT gateway per availability zone, so a zone loss does not remove egress",
            before="single_nat_gateway: true",
            after="single_nat_gateway: false",
        )

    if node.type is NodeType.relational and node.params.get("multi_az") is False:
        node.params["multi_az"] = True
        return Repair(
            rule_id="RES-002",
            node_id=node.id,
            description="Multi-AZ for a production database",
            before="multi_az: false",
            after="multi_az: true",
        )

    if node.type is NodeType.container_service and int(node.params.get("desired_count", 0)) < 2:
        before = node.params.get("desired_count", 1)
        node.params["desired_count"] = 2
        return Repair(
            rule_id="RES-002",
            node_id=node.id,
            description="At least two tasks in production, so a rolling deploy is not an outage",
            before=f"desired_count: {before}",
            after="desired_count: 2",
        )
    return None


@repair_for("SEC-005")
def _secret_rotation(graph: ResourceGraph, finding: Finding) -> Repair | None:
    """Rotating slower than the baseline has one correct answer: the baseline.

    Only the interval is repaired. A secret whose *pattern* has no rotation
    control at all is a pattern choice, not a parameter, so that variant is
    left for a human.
    """
    if not finding.node_id or "rotates every" not in finding.message:
        return None
    node = graph.node(finding.node_id)
    limit = _trailing_int(finding.message)
    if limit is None:
        return None
    before = node.params.get("rotation_days")
    node.params["rotation_days"] = limit
    return Repair(
        rule_id="SEC-005",
        node_id=node.id,
        description="Rotate at the platform's maximum interval rather than beyond it",
        before=f"rotation_days: {before}",
        after=f"rotation_days: {limit}",
    )


@repair_for("NET-006")
def _private_cidr(graph: ResourceGraph, finding: Finding) -> Repair | None:
    """Not repairable, and saying so explicitly is the point.

    A CIDR is an organisational allocation. Picking one automatically would
    produce a network that overlaps something the framework cannot see.
    """
    return None


def _trailing_int(message: str) -> int | None:
    for token in reversed(message.replace(".", " ").split()):
        if token.isdigit():
            return int(token)
    return None


def repairable(findings: list[Finding]) -> list[Finding]:
    """Findings a repair function is registered for. Registration is not a
    promise of success -- the function may still decline."""
    return [f for f in findings if f.rule_id in _REPAIRS]


def apply_repairs(
    graph: ResourceGraph, findings: list[Finding]
) -> tuple[ResourceGraph, list[Repair]]:
    """Return a repaired copy of the graph plus what was changed.

    The graph is copied rather than mutated so a failed repair cycle leaves the
    caller's original intact -- the decontamination rule, applied to data.
    """
    working = graph.model_copy(deep=True)
    applied: list[Repair] = []
    for finding in findings:
        fn = _REPAIRS.get(finding.rule_id)
        if fn is None:
            continue
        try:
            repair = fn(working, finding)
        except KeyError:
            continue
        if repair is not None:
            applied.append(repair)
    return working, applied
