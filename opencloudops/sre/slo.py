"""Derive SLOs, alerts, a dashboard and a runbook from a resolved plan.

The numbers are not invented. Each pattern in the catalog carries an ``slo``
block; this module composes those into per-node objectives, turns each
objective into a burn-rate alert with a real window, and writes the runbook
that the alert's annotation points at. A page with no runbook behind it is how
on-call rotations become miserable.

Burn-rate windows follow the multi-window alerting approach documented in the
Google SRE workbook: a fast window that catches a hard outage within minutes,
and a slow window that catches a gradual burn before the month's budget is
gone.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from opencloudops.spec import NodeType, ResolvedNode, ResolvedPlan


class BurnRateAlert(BaseModel):
    name: str
    severity: str
    long_window: str
    short_window: str
    burn_rate: float
    budget_consumed_pct: float
    rationale: str


class ServiceLevelObjective(BaseModel):
    node_id: str
    indicator: str
    objective_pct: float
    window_days: int
    latency_p95_ms: int | None = None
    error_budget_minutes: float
    alerts: list[BurnRateAlert] = Field(default_factory=list)


class SLOSet(BaseModel):
    service: str
    environment: str
    objectives: list[ServiceLevelObjective] = Field(default_factory=list)

    @property
    def tightest(self) -> ServiceLevelObjective | None:
        """The objective that will page first. Useful as the headline number."""
        if not self.objectives:
            return None
        return max(self.objectives, key=lambda o: o.objective_pct)


#: (name, severity, long window, short window, burn rate, budget consumed).
#: A 14.4x burn over an hour spends 2% of a 30-day budget -- that is a page.
#: A 3x burn over six hours spends 5% -- that is a ticket, not a 3am call.
_BURN_RATES = [
    ("fast-burn", "page", "1h", "5m", 14.4, 2.0),
    ("slow-burn", "ticket", "6h", "30m", 3.0, 5.0),
]

#: Node types that carry a user-facing SLO. A VPC or a KMS key does not fail in
#: a way a burn-rate alert describes, so giving them an objective would produce
#: alerts nobody can act on.
_SLO_BEARING = {
    NodeType.container_service,
    NodeType.function,
    NodeType.relational,
    NodeType.table,
    NodeType.queue,
    NodeType.object_store,
}


def _indicator(node: ResolvedNode) -> str:
    return {
        NodeType.container_service: "Proportion of HTTP requests served without a 5xx",
        NodeType.function: "Proportion of invocations that complete without error",
        NodeType.relational: "Proportion of connection attempts that succeed",
        NodeType.table: "Proportion of read and write requests served without throttling",
        NodeType.queue: "Proportion of messages processed before the redrive threshold",
        NodeType.object_store: "Proportion of object requests served without a 5xx",
    }.get(node.type, "Availability")


def derive_slos(plan: ResolvedPlan) -> SLOSet:
    objectives: list[ServiceLevelObjective] = []
    for node in plan.nodes:
        if node.type not in _SLO_BEARING:
            continue
        window_days = node.slo.error_budget_window_days
        objective = node.slo.availability_pct
        budget_minutes = round((100 - objective) / 100 * window_days * 24 * 60, 2)

        alerts = [
            BurnRateAlert(
                name=f"{node.id}-{name}",
                severity=severity,
                long_window=long_w,
                short_window=short_w,
                burn_rate=rate,
                budget_consumed_pct=consumed,
                rationale=(
                    f"At {rate}x the sustainable error rate, {consumed}% of the "
                    f"{window_days}-day budget is gone in {long_w}."
                ),
            )
            for name, severity, long_w, short_w, rate, consumed in _BURN_RATES
        ]
        objectives.append(
            ServiceLevelObjective(
                node_id=node.id,
                indicator=_indicator(node),
                objective_pct=objective,
                window_days=window_days,
                latency_p95_ms=node.slo.latency_p95_ms,
                error_budget_minutes=budget_minutes,
                alerts=alerts,
            )
        )
    return SLOSet(service=plan.service, environment=plan.environment, objectives=objectives)


def dashboard(plan: ResolvedPlan, slos: SLOSet) -> dict:
    """A provider-neutral dashboard description.

    Deliberately not a CloudWatch dashboard body: the emitters turn this into
    whichever backend is in play, and an operator can read it without one.
    """
    widgets = []
    for obj in slos.objectives:
        widgets.append(
            {
                "title": f"{obj.node_id} -- availability against {obj.objective_pct}%",
                "type": "slo",
                "node": obj.node_id,
                "indicator": obj.indicator,
                "error_budget_minutes": obj.error_budget_minutes,
            }
        )
        if obj.latency_p95_ms:
            widgets.append(
                {
                    "title": f"{obj.node_id} -- p95 latency against {obj.latency_p95_ms}ms",
                    "type": "latency",
                    "node": obj.node_id,
                    "threshold_ms": obj.latency_p95_ms,
                }
            )
    widgets.append(
        {
            "title": "Dependency graph",
            "type": "graph",
            "edges": [f"{e.from_} {e.relation.value} {e.to}" for e in plan.edges],
        }
    )
    return {
        "name": f"{plan.service}-{plan.environment}",
        "service": plan.service,
        "environment": plan.environment,
        "widgets": widgets,
    }


def runbook(plan: ResolvedPlan, slos: SLOSet) -> str:
    """The document every alert annotation points at.

    Generated from the plan, so it names the actual resources and the actual
    dependency order rather than a template's placeholders.
    """
    lines = [
        f"# Runbook -- {plan.service} ({plan.environment})",
        "",
        f"Generated by OpenCloudOps from graph `{plan.graph_name}`. Region `{plan.region}`.",
        "",
        "## What this service is",
        "",
        "| node | type | resource | exposure | data |",
        "|---|---|---|---|---|",
    ]
    for node in plan.nodes:
        lines.append(
            f"| `{node.id}` | `{node.type.value}` | `{node.name}` | "
            f"{node.exposure.value} | {node.data_classification.value} |"
        )

    lines += [
        "",
        "## Objectives",
        "",
        "| node | objective | error budget | indicator |",
        "|---|---|---|---|",
    ]
    for obj in slos.objectives:
        latency = f" / p95 {obj.latency_p95_ms}ms" if obj.latency_p95_ms else ""
        lines.append(
            f"| `{obj.node_id}` | {obj.objective_pct}%{latency} | "
            f"{obj.error_budget_minutes} min / {obj.window_days}d | {obj.indicator} |"
        )

    lines += [
        "",
        "## Blast radius",
        "",
        "Apply order is dependency order, so a failure upstream stalls everything after it.",
        "",
        "```",
        " -> ".join(plan.order),
        "```",
        "",
    ]
    for node in plan.nodes:
        if node.depends_on:
            lines.append(f"- `{node.id}` is unavailable if any of {', '.join(f'`{d}`' for d in node.depends_on)} is.")
    lines += [
        "",
        "## When an alert fires",
        "",
        "1. Identify which objective is burning, and how fast (page = 14.4x, ticket = 3x).",
        "2. Classify the signal: `occ diagnose <service> --signal \"<error text>\"`.",
        "   The classifier matches against the error-pattern library in",
        "   `opencloudops/sre/patterns.py` and reports its confidence.",
        "3. Ask for a proposal: `occ remediate <service> --signal \"<error text>\"`.",
        "   LOW-risk proposals can be applied by an agent; MEDIUM needs a recorded",
        "   approval; HIGH is never applied by an agent under any flag.",
        "4. Record what actually happened: `occ learn --service "
        f"{plan.service} --symptom \"...\" --cause \"...\" --guard \"...\"`.",
        "   The next run reads it back before building anything.",
        "",
        "## What is never auto-remediated",
        "",
        "- Database connection failures -- these are often a failover in progress.",
        "  Retrying converts a 30-second blip into a thundering herd.",
        "- Credential and auth failures -- rotation is a human-owned action.",
        "- Cluster-level capacity exhaustion -- not fixable per service.",
        "- Image pull failures -- never retry the same tag.",
        "- Multi-service cascades -- the root cause is not in the service that paged.",
        "",
    ]
    return "\n".join(lines)
