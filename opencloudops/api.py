"""The OpenCloudOps service.

Three route groups, deliberately separated because they carry different risk:

* ``/plan``, ``/iac``, ``/guard`` -- build-time. Read a graph, return a proposal.
  Nothing is written and nothing is applied.
* ``/sre/*`` -- the agentic SRE plane. Observe a signal, diagnose it, and get a
  remediation proposal with an honest risk tier. ``/sre/remediate`` returns a
  *proposal*; there is no apply endpoint, and adding one would need the HIGH
  gate to be moved, not merely the route.
* ``/chat`` -- the ChatOps surface, so a bot can post one line and get one reply.

Every request is one OpenTelemetry trace. The response carries its
``request_id``, which is the trace id when a collector is configured, so a
reply in Slack links directly to the spans that produced it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from opencloudops import __version__, telemetry as otel
from opencloudops.agentgraph import AgentGraphSpec, NodeStatus
from opencloudops.agents import PipelineDeps, build_runtime, load_requested_graph
from opencloudops.audit import AuditLog
from opencloudops.chatops import ChatOps
from opencloudops.emitters import available, get_emitter
from opencloudops.graph import load_platform, to_mermaid
from opencloudops.ground import PatternCatalog
from opencloudops.learner import Learner
from opencloudops.spec import ResolvedPlan

def _path(var: str, default: str) -> Path:
    """Resolved per call, not at import.

    A module-level constant bakes in whatever the environment looked like when
    the process started, which makes the service impossible to reconfigure and
    tests impossible to isolate.
    """
    return Path(os.environ.get(var, default))

app = FastAPI(
    title="OpenCloudOps",
    version=__version__,
    description=(
        "Agentic cloud operations. Grounded in an approved pattern catalog, guarded by an "
        "enforced security baseline, observable end to end. Proposes; never applies."
    ),
)

otel.configure()
otel.instrument_fastapi(app)


def get_deps() -> PipelineDeps:
    """Built per request so an edited platform contract or pattern takes effect
    without a restart -- this is a control plane, not a hot path."""
    return PipelineDeps(
        platform=load_platform(_path("OCC_PLATFORM", "platform/platform.yaml")),
        catalog=PatternCatalog.from_dir(_path("OCC_PATTERNS", "patterns")),
        learner=Learner(),
        audit=AuditLog(),
        graphs_dir=_path("OCC_GRAPHS", "graphs"),
    )


def get_spec() -> AgentGraphSpec:
    return AgentGraphSpec.load(_path("OCC_AGENT_GRAPH", "agent-graph.yaml"))


# ---------------------------------------------------------------------------
# request / response models
# ---------------------------------------------------------------------------


class PlanRequest(BaseModel):
    graph: str | None = Field(None, description="Graph name under graphs/, or a path.")
    graph_document: dict[str, Any] | None = Field(None, description="An inline resource graph.")
    environment: str = "dev"
    ticket: str | None = None
    approvals: list[str] = Field(
        default_factory=list,
        description="Gated nodes to grant for this request. 'release-executor' is refused.",
    )


class IaCRequest(PlanRequest):
    languages: list[str] = Field(default_factory=lambda: ["terraform"])
    include_content: bool = Field(
        False, description="Return file bodies, not just paths. Responses get large."
    )


class SignalRequest(BaseModel):
    service: str
    signal: str = Field(description="Error text, log line, or alarm body.")
    environment: str = "prod"
    graph: str | None = Field(None, description="Optional: resolves affected nodes by name.")


class IncidentRequest(BaseModel):
    service: str
    environment: str = "prod"
    symptom: str
    root_cause: str
    guard: str = "none recorded"


class ChatRequest(BaseModel):
    text: str
    approvals: list[str] = Field(default_factory=list)


class RunEnvelope(BaseModel):
    request_id: str
    trace_id: str | None
    status: str
    risk_tier: str
    tokens: int
    steps: list[dict[str, Any]]
    escalation: str = ""
    approvals_required: list[str] = Field(default_factory=list)


def _approvals(requested: list[str], spec: AgentGraphSpec) -> set[str]:
    """Grant every gate below HIGH; refuse HIGH categorically.

    Nothing below HIGH mutates anything outside this process -- those nodes
    produce documents, and this API never writes files or touches a cloud
    account. HIGH is reserved for an apply capability the framework does not
    have, and refusing it here by rule rather than by name means adding such a
    capability cannot be switched on from an HTTP call.
    """
    high = {name for name, node in spec.nodes.items() if (node.gate_tier and node.gate_tier.value == "HIGH")}
    asked_for_high = high & set(requested)
    if asked_for_high:
        raise HTTPException(
            status_code=403,
            detail=(
                f"{', '.join(sorted(asked_for_high))} is gated HIGH and cannot be approved over "
                "HTTP. A person carries out that action themselves."
            ),
        )
    return {name for name in spec.nodes if name not in high}


def _envelope(run) -> RunEnvelope:
    return RunEnvelope(
        request_id=run.request_id,
        trace_id=run.trace_id,
        status=run.status.value,
        risk_tier=run.risk_tier.value,
        tokens=run.tokens,
        steps=[s.model_dump(mode="json") for s in run.steps],
        escalation=run.escalation,
        approvals_required=run.approvals_required,
    )


def _payload(body: PlanRequest, deps: PipelineDeps) -> dict[str, Any]:
    """Build the agent payload, failing fast on input the caller got wrong.

    A graph name that does not resolve is a client error, so it comes back as a
    404 rather than as a 200 carrying a failed run -- the agent envelope is for
    reporting what the agents decided, not for hiding a typo.
    """
    if not body.graph and not body.graph_document:
        raise HTTPException(status_code=422, detail="supply either 'graph' or 'graph_document'")
    if body.graph and not body.graph_document:
        try:
            load_requested_graph(deps, {"graph": body.graph})
        except Exception as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
    payload: dict[str, Any] = {"environment": body.environment}
    if body.graph:
        payload["graph"] = body.graph
    if body.graph_document:
        payload["graph_document"] = body.graph_document
    if body.ticket:
        payload["ticket"] = body.ticket
    return payload


def _run_or_raise(spec, deps, intent, payload, approvals):
    try:
        return build_runtime(spec, deps).run(intent, payload, approvals=approvals)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"{type(e).__name__}: {e}") from e


# ---------------------------------------------------------------------------
# meta
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__, "tracing": otel.configure()}


@app.get("/ready")
def ready(deps: PipelineDeps = Depends(get_deps), spec: AgentGraphSpec = Depends(get_spec)) -> dict:
    """Readiness means the contracts actually load. A service that answers
    /health while its pattern catalog is unreadable is lying to its probe."""
    runtime = build_runtime(spec, deps)
    unhandled = runtime.unhandled()
    if unhandled:
        raise HTTPException(
            status_code=503, detail=f"agent graph nodes without handlers: {', '.join(unhandled)}"
        )
    return {
        "status": "ready",
        "patterns": len(deps.catalog),
        "environments": [e.name for e in deps.platform.environments],
        "agent_nodes": sorted(spec.nodes),
        "backends": available(),
    }


@app.get("/agent-graph")
def agent_graph(spec: AgentGraphSpec = Depends(get_spec)) -> dict:
    """The live agent graph: nodes, zones, gates and edges. This is the
    framework's own topology, which is what makes it inspectable."""
    return {
        "version": spec.version,
        "entry": spec.entry,
        "models": spec.models,
        "nodes": {
            name: {
                "role": node.role,
                "model": spec.models.get(node.model, node.model),
                "zone": node.zone,
                "risk_gate": node.risk_gate,
                "description": node.description.strip(),
            }
            for name, node in spec.nodes.items()
        },
        "edges": [
            {
                "from": e.from_,
                "to": e.to,
                "trigger": e.trigger,
                "gate": e.gate,
                "max_cycles": e.max_cycles,
            }
            for e in spec.edges
        ],
        "evals": spec.evals.model_dump(),
        "error_handling": spec.error_handling.model_dump(),
    }


@app.get("/patterns")
def patterns(deps: PipelineDeps = Depends(get_deps)) -> list[dict]:
    return [p.model_dump(mode="json") for p in deps.catalog.all()]


@app.get("/lessons")
def lessons(deps: PipelineDeps = Depends(get_deps)) -> list[dict]:
    return [lesson.model_dump(mode="json") for lesson in deps.learner.catalog()]


@app.get("/graphs")
def graphs() -> list[str]:
    return sorted(p.name.removesuffix(".graph.yaml") for p in _path("OCC_GRAPHS", "graphs").glob("*.graph.yaml"))


@app.get("/logs")
def logs(last: int = 50) -> list[dict]:
    return AuditLog().tail(last)


# ---------------------------------------------------------------------------
# build plane
# ---------------------------------------------------------------------------


@app.post("/plan")
def plan(
    body: PlanRequest,
    deps: PipelineDeps = Depends(get_deps),
    spec: AgentGraphSpec = Depends(get_spec),
) -> dict:
    run = _run_or_raise(spec, deps, "plan", _payload(body, deps), _approvals(body.approvals, spec))
    resolved = run.output("plan")
    return {
        "run": _envelope(run).model_dump(),
        "plan": resolved,
        "guard": run.output("guard"),
        "repairs": run.output("repairs") or [],
        "mermaid": run.output("mermaid"),
        "open_questions": run.output("open_questions") or [],
        "recalled_incidents": run.output("recalled_incidents") or [],
    }


@app.post("/guard")
def guard(
    body: PlanRequest,
    deps: PipelineDeps = Depends(get_deps),
    spec: AgentGraphSpec = Depends(get_spec),
) -> dict:
    run = _run_or_raise(spec, deps, "validate", _payload(body, deps), _approvals(body.approvals, spec))
    return {
        "run": _envelope(run).model_dump(),
        "guard": run.output("guard"),
        "lessons": run.output("lessons") or [],
        "risk_tier": run.output("risk_tier"),
    }


@app.post("/iac")
def iac(
    body: IaCRequest,
    deps: PipelineDeps = Depends(get_deps),
    spec: AgentGraphSpec = Depends(get_spec),
) -> dict:
    unknown = [lang for lang in body.languages if lang not in available()]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"unknown backend(s) {', '.join(unknown)}; available: {', '.join(available())}",
        )
    payload = _payload(body, deps)
    payload["languages"] = body.languages
    run = _run_or_raise(spec, deps, "plan", payload, _approvals(body.approvals, spec))
    if run.status is not NodeStatus.ok:
        return {"run": _envelope(run).model_dump(), "artifacts": {}}

    resolved = ResolvedPlan.model_validate(run.output("plan"))
    artifacts: dict[str, Any] = {}
    for language in body.languages:
        files = get_emitter(language).emit(resolved, deps.platform)
        artifacts[language] = (
            files if body.include_content else {k: len(v) for k, v in sorted(files.items())}
        )
    return {
        "run": _envelope(run).model_dump(),
        "guard": run.output("guard"),
        "artifacts": artifacts,
        "content_included": body.include_content,
    }


# ---------------------------------------------------------------------------
# agentic SRE plane
# ---------------------------------------------------------------------------


@app.post("/sre/observe")
def sre_observe(
    body: PlanRequest,
    deps: PipelineDeps = Depends(get_deps),
    spec: AgentGraphSpec = Depends(get_spec),
) -> dict:
    """Derive the SRE setup for a plan: objectives, error budgets, burn-rate
    alerts, a dashboard and the runbook every alert should point at."""
    run = _run_or_raise(spec, deps, "plan", _payload(body, deps), _approvals(body.approvals, spec))
    return {
        "run": _envelope(run).model_dump(),
        "slos": run.output("slos"),
        "dashboard": run.output("dashboard"),
        "runbook": run.output("runbook"),
    }


@app.post("/sre/diagnose")
def sre_diagnose(
    body: SignalRequest,
    deps: PipelineDeps = Depends(get_deps),
    spec: AgentGraphSpec = Depends(get_spec),
) -> dict:
    """Classify a production signal. Returns an honest 'unrecognised' rather
    than a confident guess when nothing matches."""
    payload: dict[str, Any] = {
        "service": body.service,
        "signal": body.signal,
        "environment": body.environment,
    }
    if body.graph:
        payload["graph"] = body.graph
    run = _run_or_raise(spec, deps, "triage", payload, _approvals([], spec))
    return {"run": _envelope(run).model_dump(), "diagnosis": run.output("diagnosis")}


@app.post("/sre/remediate")
def sre_remediate(
    body: SignalRequest,
    deps: PipelineDeps = Depends(get_deps),
    spec: AgentGraphSpec = Depends(get_spec),
) -> dict:
    """Propose a remediation, with the risk tier and the reason it is or is not
    something an agent may carry out.

    There is no apply endpoint. Applying is the ``release-executor`` node, gated
    HIGH, and that gate is not grantable over HTTP.
    """
    payload: dict[str, Any] = {
        "service": body.service,
        "signal": body.signal,
        "environment": body.environment,
    }
    if body.graph:
        payload["graph"] = body.graph
    run = _run_or_raise(spec, deps, "triage", payload, _approvals([], spec))
    return {
        "run": _envelope(run).model_dump(),
        "diagnosis": run.output("diagnosis"),
        "proposal": run.output("proposal"),
        "applied": False,
    }


@app.post("/sre/incident")
def sre_incident(
    body: IncidentRequest,
    deps: PipelineDeps = Depends(get_deps),
    spec: AgentGraphSpec = Depends(get_spec),
) -> dict:
    """Record what actually happened. The next build recalls it before planning."""
    run = _run_or_raise(
        spec,
        deps,
        "learn",
        {
            "environment": body.environment,
            "service": body.service,
            "incident": body.model_dump(),
        },
        _approvals([], spec),
    )
    return {"run": _envelope(run).model_dump(), "recorded": run.output("recorded") or []}


@app.get("/sre/memory")
def sre_memory(service: str | None = None, limit: int = 20, deps: PipelineDeps = Depends(get_deps)) -> list[dict]:
    return [m.model_dump(mode="json") for m in deps.learner.recall(service=service, limit=limit)]


@app.post("/promote")
def promote(
    body: PlanRequest,
    deps: PipelineDeps = Depends(get_deps),
    spec: AgentGraphSpec = Depends(get_spec),
) -> dict:
    """Produce the change record for a promotion. ``applied`` is always false."""
    run = _run_or_raise(spec, deps, "promote", _payload(body, deps), _approvals(body.approvals, spec))
    return {"run": _envelope(run).model_dump(), "promotion": run.output("promotion")}


# ---------------------------------------------------------------------------
# chatops
# ---------------------------------------------------------------------------


@app.post("/chat")
def chat(
    body: ChatRequest,
    deps: PipelineDeps = Depends(get_deps),
    spec: AgentGraphSpec = Depends(get_spec),
) -> dict:
    """One line in, one markdown reply out. What a Slack or Telegram bot posts to."""
    session = ChatOps(deps, spec)
    session.approvals = _approvals(body.approvals, spec) - {"release-executor"}
    reply = session.handle(body.text)
    return reply.model_dump()


@app.get("/graph/{name}/mermaid")
def graph_mermaid(
    name: str,
    environment: str = "dev",
    deps: PipelineDeps = Depends(get_deps),
    spec: AgentGraphSpec = Depends(get_spec),
) -> dict:
    run = _run_or_raise(
        spec, deps, "plan", {"graph": name, "environment": environment}, _approvals([], spec)
    )
    resolved = run.output("plan")
    if not resolved:
        raise HTTPException(status_code=404, detail=f"no resolvable plan for {name!r}")
    return {"mermaid": to_mermaid(ResolvedPlan.model_validate(resolved))}
