"""Agent graph engineering: the declarative graph, and the runtime that runs it.

The graph is the framework. Rather than a hard-coded pipeline, the agents,
their zones, the edges between them, the gates on those edges, the repair
cycles, the failure policy and the token budgets all live in
``agent-graph.yaml``. Changing how the system behaves is an edit to that file.

Concepts carried forward from the framework this replaces, now executable:

**Zone defence** -- each node owns a disjoint zone of concerns and may not act
outside it. Two nodes claiming the same zone is a validation error, because
overlapping ownership is how two agents fight over one file.

**Gates** -- an edge may carry ``gate: must-pass``. The downstream node's
verdict then decides whether the run continues. This is why the builder cannot
ship IaC the validator rejected: the edge, not the builder's own judgement,
decides.

**Repair cycles** -- a back-edge with ``max_cycles`` lets the validator hand
work back to the builder a bounded number of times. Bounded is the point; an
unbounded fix-loop is how an agent burns an afternoon converging on nothing.

**Error decontamination** -- on failure the node's local state is dropped and
context is reloaded from source rather than carried forward. Retrying with the
context that caused the failure is how three retries fail identically.

**Token budgets** -- per-node and per-run ceilings, enforced, because "the
agent scanned the whole workspace" is a cost bug you only catch if you measure.

Every node execution is an OpenTelemetry span; the run's trace id is its
request id. See ``opencloudops/telemetry.py``.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

from opencloudops import telemetry as otel
from opencloudops.audit import AuditLog
from opencloudops.spec import RiskTier


class NodeStatus(str, Enum):
    ok = "ok"
    failed = "failed"
    blocked = "blocked"
    skipped = "skipped"
    awaiting_approval = "awaiting-approval"


# ---------------------------------------------------------------------------
# declarative spec
# ---------------------------------------------------------------------------


class AgentNodeSpec(BaseModel):
    role: str
    model: str = "planner"
    zone: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    risk_gate: str = "none"
    description: str = ""

    @property
    def gate_tier(self) -> RiskTier | None:
        value = self.risk_gate.upper()
        if value in {"NONE", ""}:
            return None
        try:
            return RiskTier(value)
        except ValueError:
            raise ValueError(f"risk_gate must be none|LOW|MEDIUM|HIGH, got {self.risk_gate!r}") from None


class AgentEdgeSpec(BaseModel):
    from_: str = Field(alias="from")
    to: str
    trigger: str = "always"
    gate: str | None = None
    max_cycles: int | None = None

    model_config = {"populate_by_name": True}

    @property
    def triggers(self) -> list[str]:
        """``"build | scaffold | edit-tf"`` -> the three intents it matches."""
        return [t.strip().lower() for t in self.trigger.split("|") if t.strip()]

    def matches(self, intent: str) -> bool:
        # A repair edge (one carrying max_cycles) is routed by the upstream
        # node's `needs_repair` verdict, not by the user's intent -- the
        # request that started the run said "plan", not "go fix yourself".
        if self.max_cycles is not None:
            return True
        if self.trigger.strip().lower() in {"always", "always_first"}:
            return True
        return intent.strip().lower() in self.triggers


class FailurePolicy(BaseModel):
    retry: int = 1
    backoff: str = "exponential"
    fallback: str = "escalate_to_human"


class FailurePolicySpec(BaseModel):
    default: FailurePolicy = Field(default_factory=FailurePolicy)
    per_node: dict[str, FailurePolicy] = Field(default_factory=dict)

    def for_node(self, node: str) -> FailurePolicy:
        return self.per_node.get(node, self.default)


class DecontaminationSpec(BaseModel):
    on_node_failure: str = "snapshot context, isolate, do not carry forward"
    on_retry: str = "reload context fresh -- never reuse stale context"
    max_error_chain: int = 3
    after_max: str = "hard reset -- discard in-flight work, escalate clean"


class EvalSpec(BaseModel):
    per_node_warn: int = 50_000
    per_node_hard: int = 100_000
    per_run_hard: int = 500_000


class GovernanceSpec(BaseModel):
    ticket_required: bool = False
    ticket_pattern: str = r"[A-Z][A-Z0-9]+-\d+"
    report_only_on_confirmed: bool = True
    knowledge_scope: list[str] = Field(
        default_factory=lambda: ["services", "architecture", "runbooks", "contracts", "policies"]
    )


class AgentGraphSpec(BaseModel):
    version: str = "2.0"
    type: str = "org"
    description: str = ""
    entry: str = "orchestrator"
    models: dict[str, str] = Field(default_factory=dict)
    nodes: dict[str, AgentNodeSpec]
    edges: list[AgentEdgeSpec] = Field(default_factory=list)
    error_handling: DecontaminationSpec = Field(default_factory=DecontaminationSpec)
    failure_policy: FailurePolicySpec = Field(default_factory=FailurePolicySpec)
    evals: EvalSpec = Field(default_factory=EvalSpec)
    governance: GovernanceSpec = Field(default_factory=GovernanceSpec)

    @model_validator(mode="after")
    def _coherent(self) -> AgentGraphSpec:
        if self.entry not in self.nodes:
            raise ValueError(f"entry node {self.entry!r} is not defined in nodes")
        for e in self.edges:
            if e.from_ not in self.nodes:
                raise ValueError(f"edge from unknown node {e.from_!r}")
            if e.to not in self.nodes:
                raise ValueError(f"edge to unknown node {e.to!r}")
        # Zone defence: disjoint ownership, checked rather than asserted in prose.
        seen: dict[str, str] = {}
        for name, node in self.nodes.items():
            for zone in node.zone:
                if zone in seen and seen[zone] != name:
                    raise ValueError(
                        f"zone {zone!r} is claimed by both {seen[zone]!r} and {name!r}. "
                        "Zones must be disjoint -- two agents owning one concern is how they collide."
                    )
                seen[zone] = name
        for name, node in self.nodes.items():
            node.gate_tier  # raises on an unparseable risk_gate
        return self

    @classmethod
    def load(cls, path: Path | str) -> AgentGraphSpec:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"no agent graph at {path}")
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        header = raw.get("graph", {})
        body = {
            "version": header.get("version", "2.0"),
            "type": header.get("type", "org"),
            "description": header.get("description", ""),
            "entry": header.get("entry", raw.get("entry", "orchestrator")),
            "models": raw.get("models", {}),
            "nodes": raw.get("nodes", {}),
            "edges": raw.get("edges", []),
        }
        if "error_handling" in raw:
            body["error_handling"] = raw["error_handling"].get(
                "decontamination", raw["error_handling"]
            )
        for key in ("failure_policy", "evals", "governance"):
            if key in raw:
                body[key] = raw[key]
        return cls.model_validate(body)

    def outgoing(self, node: str, intent: str) -> list[AgentEdgeSpec]:
        return [e for e in self.edges if e.from_ == node and e.matches(intent)]


# ---------------------------------------------------------------------------
# runtime
# ---------------------------------------------------------------------------


class NodeResult(BaseModel):
    node: str
    status: NodeStatus
    summary: str = ""
    tokens: int = 0
    duration_ms: int = 0
    cycle: int = 0
    retries: int = 0
    decontaminated: bool = False
    outputs: dict[str, Any] = Field(default_factory=dict)
    needs_repair: bool = False
    repair_reason: str = ""


class AgentRun(BaseModel):
    request_id: str
    trace_id: str | None = None
    intent: str
    status: NodeStatus = NodeStatus.ok
    steps: list[NodeResult] = Field(default_factory=list)
    tokens: int = 0
    risk_tier: RiskTier = RiskTier.low
    escalation: str = ""
    approvals_required: list[str] = Field(default_factory=list)

    def output(self, key: str, default: Any = None) -> Any:
        """Last value any node published under ``key``."""
        for step in reversed(self.steps):
            if key in step.outputs:
                return step.outputs[key]
        return default


class AgentContext:
    """Per-run state. ``state`` is shared between nodes; ``scratch`` is
    node-local and is what decontamination throws away on a retry."""

    def __init__(
        self,
        *,
        request_id: str,
        intent: str,
        payload: dict[str, Any],
        audit: AuditLog,
        approvals: set[str] | None = None,
    ) -> None:
        self.request_id = request_id
        self.intent = intent
        self.payload = payload
        self.audit = audit
        self.approvals = approvals or set()
        self.state: dict[str, Any] = {}
        self.scratch: dict[str, Any] = {}
        self.error_chain: int = 0

    def decontaminate(self, node: str, reason: str) -> None:
        """Drop node-local state so a retry starts from source, not from the
        context that produced the failure."""
        self.scratch.clear()
        self.error_chain += 1
        self.audit.log(
            "decontaminate",
            node=node,
            reason=reason,
            error_chain=self.error_chain,
            request_id=self.request_id,
        )


Handler = Callable[[AgentContext], NodeResult]


class TokenBudgetExceeded(Exception):
    """Raised when a node or run blows its eval ceiling."""


class AgentRuntime:
    """Executes an ``AgentGraphSpec``. Handlers are registered per node id."""

    def __init__(
        self,
        spec: AgentGraphSpec,
        *,
        audit: AuditLog | None = None,
        autonomous_up_to: RiskTier = RiskTier.low,
    ) -> None:
        self.spec = spec
        self.audit = audit or AuditLog()
        self.autonomous_up_to = autonomous_up_to
        self.handlers: dict[str, Handler] = {}

    def register(self, node: str) -> Callable[[Handler], Handler]:
        if node not in self.spec.nodes:
            raise KeyError(f"cannot register a handler for undeclared node {node!r}")

        def wrap(fn: Handler) -> Handler:
            self.handlers[node] = fn
            return fn

        return wrap

    def unhandled(self) -> list[str]:
        return sorted(set(self.spec.nodes) - set(self.handlers))

    # -- gates ----------------------------------------------------------

    _TIER_ORDER = {RiskTier.low: 0, RiskTier.medium: 1, RiskTier.high: 2}

    def _gate_allows(self, node: str, ctx: AgentContext) -> tuple[bool, str]:
        tier = self.spec.nodes[node].gate_tier
        if tier is None:
            return True, "no gate"
        if node in ctx.approvals:
            return True, f"{tier.value} approved for this run"
        if self._TIER_ORDER[tier] <= self._TIER_ORDER[self.autonomous_up_to]:
            return True, f"{tier.value} within autonomy ceiling"
        return False, (
            f"{node} is gated at {tier.value}; this runtime acts autonomously only up to "
            f"{self.autonomous_up_to.value}. A recorded approval is required."
        )

    # -- governance -----------------------------------------------------

    def _governance_ok(self, ctx: AgentContext) -> tuple[bool, str]:
        gov = self.spec.governance
        if not gov.ticket_required:
            return True, ""
        import re

        ticket = str(ctx.payload.get("ticket", ""))
        if ticket and re.fullmatch(gov.ticket_pattern, ticket):
            return True, ""
        return False, (
            f"governance requires a ticket matching {gov.ticket_pattern!r} before any node runs; "
            f"got {ticket!r}."
        )

    # -- execution ------------------------------------------------------

    def _run_node(self, node: str, ctx: AgentContext, cycle: int) -> NodeResult:
        spec = self.spec.nodes[node]
        policy = self.spec.failure_policy.for_node(node)
        handler = self.handlers.get(node)
        if handler is None:
            return NodeResult(
                node=node,
                status=NodeStatus.skipped,
                summary=f"no handler registered for {node!r}",
                cycle=cycle,
            )

        attempt = 0
        decontaminated = False
        while True:
            started = time.monotonic()
            with otel.span(
                f"agent.{node}",
                **{
                    otel.ATTR_REQUEST_ID: ctx.request_id,
                    otel.ATTR_NODE: node,
                    otel.ATTR_ROLE: spec.role,
                    otel.ATTR_MODEL: self.spec.models.get(spec.model, spec.model),
                    otel.ATTR_ZONE: ",".join(spec.zone),
                    otel.ATTR_RISK_GATE: spec.risk_gate,
                    otel.ATTR_CYCLE: cycle,
                    otel.ATTR_RETRY: attempt,
                    otel.ATTR_DECONTAMINATED: decontaminated,
                },
            ) as sp:
                try:
                    result = handler(ctx)
                    result.cycle = cycle
                    result.retries = attempt
                    result.decontaminated = decontaminated
                    result.duration_ms = int((time.monotonic() - started) * 1000)
                    sp.set_attribute(otel.ATTR_TOKENS, result.tokens)
                    sp.set_attribute(otel.ATTR_GATE_RESULT, result.status.value)
                    self._enforce_node_budget(node, result, sp)
                    self.audit.log(
                        "agent_node",
                        node=node,
                        status=result.status.value,
                        cycle=cycle,
                        retries=attempt,
                        tokens=result.tokens,
                        summary=result.summary,
                        request_id=ctx.request_id,
                    )
                    return result
                except TokenBudgetExceeded:
                    raise
                except Exception as exc:
                    self.audit.log(
                        "agent_node",
                        status="error",
                        node=node,
                        attempt=attempt,
                        error=str(exc),
                        request_id=ctx.request_id,
                    )
                    if attempt >= policy.retry or ctx.error_chain >= self.spec.error_handling.max_error_chain:
                        return NodeResult(
                            node=node,
                            status=NodeStatus.failed,
                            summary=f"{type(exc).__name__}: {exc}",
                            cycle=cycle,
                            retries=attempt,
                            decontaminated=decontaminated,
                            outputs={"fallback": policy.fallback},
                        )
                    ctx.decontaminate(node, reason=str(exc))
                    decontaminated = True
                    attempt += 1
                    if policy.backoff == "exponential":
                        time.sleep(min(0.05 * (2**attempt), 0.5))

    def _enforce_node_budget(self, node: str, result: NodeResult, sp: Any) -> None:
        evals = self.spec.evals
        sp.set_attribute(otel.ATTR_TOKEN_BUDGET, evals.per_node_hard)
        if result.tokens > evals.per_node_hard:
            raise TokenBudgetExceeded(
                f"{node} used {result.tokens} tokens, over the hard ceiling of {evals.per_node_hard}. "
                "This is nearly always a full-workspace scan where an index lookup would do."
            )
        if result.tokens > evals.per_node_warn:
            sp.add_event(
                "token-budget-warning",
                {"node": node, "tokens": result.tokens, "warn_at": evals.per_node_warn},
            )
            self.audit.log(
                "token_budget",
                status="warn",
                node=node,
                tokens=result.tokens,
                warn_at=evals.per_node_warn,
            )

    def run(
        self,
        intent: str,
        payload: dict[str, Any] | None = None,
        *,
        approvals: set[str] | None = None,
        request_id: str | None = None,
    ) -> AgentRun:
        """Execute the graph for one request, starting at the entry node."""
        request_id = request_id or otel.new_request_id()
        ctx = AgentContext(
            request_id=request_id,
            intent=intent,
            payload=payload or {},
            audit=self.audit,
            approvals=approvals,
        )

        with otel.span(
            "occ.request",
            **{
                otel.ATTR_REQUEST_ID: request_id,
                "occ.intent": intent,
                otel.ATTR_GRAPH: str((payload or {}).get("graph", "")),
                otel.ATTR_ENVIRONMENT: str((payload or {}).get("environment", "")),
            },
        ) as root:
            trace_id = otel.current_trace_id()
            if trace_id:
                ctx.request_id = request_id = trace_id
                root.set_attribute(otel.ATTR_REQUEST_ID, request_id)
            run = AgentRun(request_id=request_id, trace_id=trace_id, intent=intent)
            self.audit.log("request_start", intent=intent, request_id=request_id)

            ok, why = self._governance_ok(ctx)
            if not ok:
                run.status = NodeStatus.blocked
                run.escalation = why
                run.steps.append(
                    NodeResult(node=self.spec.entry, status=NodeStatus.blocked, summary=why)
                )
                self.audit.log("request_end", status="blocked", reason=why, request_id=request_id)
                return run

            cycles: dict[tuple[str, str], int] = {}
            queue: list[tuple[str, int]] = [(self.spec.entry, 0)]
            visited_budget = 0

            while queue:
                node, cycle = queue.pop(0)
                visited_budget += 1
                if visited_budget > 64:
                    run.status = NodeStatus.failed
                    run.escalation = "agent graph exceeded 64 node executions -- routing is looping"
                    break

                allowed, why = self._gate_allows(node, ctx)
                if not allowed:
                    run.steps.append(
                        NodeResult(node=node, status=NodeStatus.awaiting_approval, summary=why, cycle=cycle)
                    )
                    run.approvals_required.append(node)
                    run.status = NodeStatus.awaiting_approval
                    run.risk_tier = self.spec.nodes[node].gate_tier or RiskTier.medium
                    self.audit.log("gate", status="held", node=node, reason=why, request_id=request_id)
                    continue

                try:
                    result = self._run_node(node, ctx, cycle)
                except TokenBudgetExceeded as e:
                    run.status = NodeStatus.failed
                    run.escalation = str(e)
                    run.steps.append(
                        NodeResult(node=node, status=NodeStatus.failed, summary=str(e), cycle=cycle)
                    )
                    break

                run.steps.append(result)
                run.tokens += result.tokens
                if run.tokens > self.spec.evals.per_run_hard:
                    run.status = NodeStatus.failed
                    run.escalation = (
                        f"run used {run.tokens} tokens, over the per-run ceiling of "
                        f"{self.spec.evals.per_run_hard}"
                    )
                    break

                if result.status is NodeStatus.failed:
                    run.status = NodeStatus.failed
                    run.escalation = result.outputs.get("fallback", "escalate_to_human")
                    break
                if result.status is NodeStatus.blocked:
                    run.status = NodeStatus.blocked
                    run.escalation = result.summary
                    break

                for edge in self.spec.outgoing(node, intent):
                    if edge.to == edge.from_:
                        continue  # self-edges are governance markers, not routing
                    if result.needs_repair and edge.max_cycles is None:
                        # Mid-repair, only the repair edge fires. Letting the
                        # downstream edges run here would derive SLOs and write
                        # memories from a plan this node has just rejected.
                        continue
                    if edge.max_cycles is not None:
                        if not result.needs_repair:
                            continue
                        key = (edge.from_, edge.to)
                        cycles[key] = cycles.get(key, 0) + 1
                        if cycles[key] > edge.max_cycles:
                            run.status = NodeStatus.blocked
                            run.escalation = (
                                f"{edge.from_} -> {edge.to} hit its {edge.max_cycles}-cycle repair "
                                f"limit without converging: {result.repair_reason or 'still failing'}"
                            )
                            self.audit.log(
                                "repair_limit",
                                status="blocked",
                                edge=f"{edge.from_}->{edge.to}",
                                request_id=request_id,
                            )
                            queue.clear()
                            break
                        queue.append((edge.to, cycles[key]))
                        continue
                    if edge.gate and result.status is not NodeStatus.ok:
                        continue
                    queue.append((edge.to, cycle))

            run.risk_tier = self._run_risk(run)
            root.set_attribute(otel.ATTR_RISK_TIER, run.risk_tier.value)
            root.set_attribute(otel.ATTR_TOKENS, run.tokens)
            self.audit.log(
                "request_end",
                status=run.status.value,
                tokens=run.tokens,
                steps=len(run.steps),
                risk_tier=run.risk_tier.value,
                request_id=request_id,
            )
            return run

    def _run_risk(self, run: AgentRun) -> RiskTier:
        if run.status in {NodeStatus.failed, NodeStatus.blocked}:
            return RiskTier.high
        tiers = [
            self.spec.nodes[s.node].gate_tier
            for s in run.steps
            if s.node in self.spec.nodes and self.spec.nodes[s.node].gate_tier
        ]
        if not tiers:
            return RiskTier.low
        return max(tiers, key=lambda t: self._TIER_ORDER[t])
