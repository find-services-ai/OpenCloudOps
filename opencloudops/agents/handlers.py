"""Node handlers for the OpenCloudOps agent graph.

One function per node in ``agent-graph.yaml``. Each one:

* reads what it needs from ``ctx.state`` (shared) or ``ctx.payload`` (request),
* does its work with the deterministic core -- ground, name, guard, emit, derive,
* publishes named outputs other nodes read,
* reports an honest status.

Token accounting is real, not decorative: ``NodeResult.tokens`` is charged from
the size of the material a node actually handled, which is what the evals in
the graph spec are trying to bound. A node that scans the whole workspace shows
up as a token spike on its span, which is exactly the failure mode the original
framework's evals section was written to catch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from opencloudops import repair as repair_mod
from opencloudops.agentgraph import (
    AgentContext,
    AgentGraphSpec,
    AgentRuntime,
    NodeResult,
    NodeStatus,
)
from opencloudops.audit import AuditLog
from opencloudops.emitters import available, get_emitter
from opencloudops.graph import GraphError, load_graph, resolve, to_mermaid
from opencloudops.ground import GroundingError, PatternCatalog
from opencloudops.guard import evaluate
from opencloudops.learner import JUNCTION_QUESTIONS, Learner, Memory
from opencloudops.spec import PlatformContract, ResourceGraph
from opencloudops.sre import derive_slos, diagnose, propose, runbook
from opencloudops.sre.slo import dashboard

#: Rough characters-per-token. Used only to charge a cost against a budget --
#: it does not need to be exact, it needs to be consistent.
_CHARS_PER_TOKEN = 4


def _tokens(*blobs: Any) -> int:
    total = 0
    for blob in blobs:
        if blob is None:
            continue
        total += len(blob if isinstance(blob, str) else str(blob))
    return total // _CHARS_PER_TOKEN


@dataclass
class PipelineDeps:
    """Everything the handlers need, injected rather than imported.

    Tests construct this with an in-memory audit log and a temporary memory
    directory, which is why nothing here reaches for a module-level singleton.
    """

    platform: PlatformContract
    catalog: PatternCatalog
    learner: Learner
    audit: AuditLog
    graphs_dir: Path = field(default_factory=lambda: Path("graphs"))


# ---------------------------------------------------------------------------
# orchestrator
# ---------------------------------------------------------------------------


def load_requested_graph(deps: PipelineDeps, payload: dict[str, Any]) -> ResourceGraph:
    """Accept a graph as an inline document, a path, or a name under graphs/."""
    if isinstance(payload.get("graph_document"), dict):
        return ResourceGraph.model_validate(payload["graph_document"])

    name = payload.get("graph")
    if not name:
        raise GraphError(
            "no graph supplied. Pass `graph` (a name or path) or `graph_document` (inline YAML)."
        )
    candidate = Path(str(name))
    if candidate.exists():
        return load_graph(candidate)
    for suffix in (".graph.yaml", ".graph.yml", ".yaml", ".yml"):
        path = deps.graphs_dir / f"{name}{suffix}"
        if path.exists():
            return load_graph(path)
    known = sorted(p.name for p in deps.graphs_dir.glob("*.graph.yaml"))
    raise GraphError(
        f"no graph named {name!r} in {deps.graphs_dir}/ (have: {', '.join(known) or 'none'})"
    )


def orchestrator(deps: PipelineDeps) -> Any:
    def handler(ctx: AgentContext) -> NodeResult:
        payload = ctx.payload
        environment = payload.get("environment", "dev")

        # Triage and knowledge intents carry no graph; they route straight past
        # the builder, so demanding one here would break those paths.
        needs_graph = ctx.intent.lower() in {
            "plan", "build", "provision", "scaffold", "iac", "validate", "check", "policy", "guard",
            "promote", "apply", "release",
        }

        graph: ResourceGraph | None = None
        unanswered: list[dict[str, str]] = []
        if needs_graph:
            graph = load_requested_graph(deps, payload)
            ctx.state["graph"] = graph
            # Junction questions whose answers the graph does not already carry.
            # Asked here, at the only point where answering is cheap.
            answered = {
                "data-classification": bool(graph.metadata.data_classification),
                "environments": bool(graph.metadata.environments),
                "public-surface": any(n.exposure.value == "public" for n in graph.spec.nodes)
                or True,
                "ticket": bool(payload.get("ticket")),
                "db-auth": True,
                "shared-image": True,
            }
            unanswered = [q for q in JUNCTION_QUESTIONS if not answered.get(q["id"], True)]

        service = graph.metadata.service if graph else str(payload.get("service", ""))
        memories = deps.learner.recall(service=service or None, limit=10)
        ctx.state["memories"] = memories
        ctx.state["environment"] = environment

        summary = (
            f"intent={ctx.intent} environment={environment}"
            + (f" graph={graph.metadata.name}" if graph else "")
            + (f" recalled {len(memories)} prior incident(s)" if memories else "")
        )
        return NodeResult(
            node="orchestrator",
            status=NodeStatus.ok,
            summary=summary,
            tokens=_tokens(summary, memories),
            outputs={
                "environment": environment,
                "graph_name": graph.metadata.name if graph else None,
                "service": service,
                "open_questions": unanswered,
                "recalled_incidents": [m.model_dump(mode="json") for m in memories],
            },
        )

    return handler


# ---------------------------------------------------------------------------
# iac-builder
# ---------------------------------------------------------------------------


def iac_builder(deps: PipelineDeps) -> Any:
    def handler(ctx: AgentContext) -> NodeResult:
        graph: ResourceGraph | None = ctx.state.get("graph")
        if graph is None:
            return NodeResult(
                node="iac-builder",
                status=NodeStatus.blocked,
                summary="no resource graph in context; the orchestrator did not load one",
            )
        environment = ctx.state.get("environment", "dev")
        languages = ctx.payload.get("languages") or [ctx.payload.get("language", "terraform")]
        unknown = [lang for lang in languages if lang not in available()]
        if unknown:
            return NodeResult(
                node="iac-builder",
                status=NodeStatus.blocked,
                summary=(
                    f"unknown IaC backend(s): {', '.join(unknown)}. "
                    f"Available: {', '.join(available())}"
                ),
            )

        try:
            plan = resolve(graph, deps.platform, deps.catalog, environment)
        except GroundingError as e:
            # Grounding failures are the fail-closed path: no approved pattern
            # means no infrastructure, and the message names the gap.
            return NodeResult(
                node="iac-builder",
                status=NodeStatus.blocked,
                summary=f"not grounded: {e}",
                outputs={"grounding_error": str(e)},
            )

        artifacts: dict[str, dict[str, str]] = {}
        for language in languages:
            artifacts[language] = get_emitter(language).emit(plan, deps.platform)

        ctx.state["plan"] = plan
        ctx.state["artifacts"] = artifacts

        file_count = sum(len(v) for v in artifacts.values())
        repairs = ctx.state.get("repairs_applied", [])
        summary = (
            f"{len(plan.nodes)} node(s) grounded, {file_count} file(s) across "
            f"{', '.join(languages)}"
            + (f" after {len(repairs)} repair(s)" if repairs else "")
        )
        return NodeResult(
            node="iac-builder",
            status=NodeStatus.ok,
            summary=summary,
            tokens=_tokens(*(c for files in artifacts.values() for c in files.values())),
            outputs={
                "plan": plan.model_dump(mode="json"),
                "artifact_paths": {
                    lang: sorted(files) for lang, files in artifacts.items()
                },
                "mermaid": to_mermaid(plan),
                "repairs_applied": [r.model_dump(mode="json") for r in repairs],
            },
        )

    return handler


# ---------------------------------------------------------------------------
# iac-validator
# ---------------------------------------------------------------------------


def iac_validator(deps: PipelineDeps) -> Any:
    def handler(ctx: AgentContext) -> NodeResult:
        plan = ctx.state.get("plan")
        graph: ResourceGraph | None = ctx.state.get("graph")
        if plan is None:
            # Reached via a `validate` intent that skipped the builder: resolve
            # here rather than refusing, so `occ guard` works on its own.
            if graph is None:
                return NodeResult(
                    node="iac-validator",
                    status=NodeStatus.blocked,
                    summary="nothing to validate: neither a plan nor a graph is in context",
                )
            plan = resolve(graph, deps.platform, deps.catalog, ctx.state.get("environment", "dev"))
            ctx.state["plan"] = plan

        report = evaluate(plan, deps.platform)
        artifacts: dict[str, dict[str, str]] = ctx.state.get("artifacts", {})
        hits = []
        for files in artifacts.values():
            hits.extend(deps.learner.scan(files, plan, deps.platform))
        blocking_lessons = deps.learner.blocking(hits)

        ctx.state["guard_report"] = report
        ctx.state["lesson_hits"] = hits

        outputs = {
            "guard": report.model_dump(mode="json"),
            "lessons": [h.model_dump(mode="json") for h in hits],
            "risk_tier": report.risk_tier(deps.platform).value,
        }

        if not report.blocked and not blocking_lessons:
            return NodeResult(
                node="iac-validator",
                status=NodeStatus.ok,
                summary=f"{report.summary()}; {len(hits)} lesson note(s)",
                tokens=_tokens(report.model_dump_json()),
                outputs=outputs,
            )

        # Blocked. Decide whether this is a repair the builder can make.
        fixable = repair_mod.repairable(report.blocks)
        if fixable and graph is not None:
            repaired, applied = repair_mod.apply_repairs(graph, fixable)
            if applied:
                ctx.state["graph"] = repaired
                ctx.state["repairs_applied"] = applied
                ctx.state.pop("plan", None)
                ctx.state.pop("artifacts", None)
                return NodeResult(
                    node="iac-validator",
                    status=NodeStatus.ok,
                    summary=(
                        f"{len(report.blocks)} blocking finding(s); "
                        f"repaired {len(applied)} in the graph and handing back to the builder"
                    ),
                    tokens=_tokens(report.model_dump_json()),
                    needs_repair=True,
                    repair_reason="; ".join(f"{r.rule_id} {r.node_id}: {r.after}" for r in applied),
                    outputs={
                        **outputs,
                        "repairs": [r.model_dump(mode="json") for r in applied],
                    },
                )

        detail = "; ".join(
            f"{f.rule_id} {f.node_id or ''}: {f.message}".strip() for f in report.blocks[:3]
        )
        for hit in blocking_lessons[:2]:
            detail += f"; {hit.lesson_id} ({hit.artifact}): {hit.message}"
        return NodeResult(
            node="iac-validator",
            status=NodeStatus.blocked,
            summary=f"refused: {detail}",
            tokens=_tokens(report.model_dump_json()),
            outputs=outputs,
        )

    return handler


# ---------------------------------------------------------------------------
# sre-observer
# ---------------------------------------------------------------------------


def sre_observer(deps: PipelineDeps) -> Any:
    def handler(ctx: AgentContext) -> NodeResult:
        signal = ctx.payload.get("signal")
        plan = ctx.state.get("plan")
        environment = ctx.state.get("environment", "dev")

        if signal:
            service = (plan.service if plan else ctx.payload.get("service", "unknown"))
            d = diagnose(signal, service=service, environment=environment, plan=plan)
            proposal = propose(d, deps.platform, plan)
            ctx.state["diagnosis"] = d
            ctx.state["proposal"] = proposal
            return NodeResult(
                node="sre-observer",
                status=NodeStatus.ok,
                summary=(
                    f"{d.pattern_id or 'unrecognised'} at {d.confidence.value} confidence; "
                    f"remediation {'autonomous' if proposal.autonomous else 'needs a human'}"
                ),
                tokens=_tokens(signal, d.model_dump_json()),
                outputs={
                    "diagnosis": d.model_dump(mode="json"),
                    "proposal": proposal.model_dump(mode="json"),
                },
            )

        if plan is None:
            return NodeResult(
                node="sre-observer",
                status=NodeStatus.skipped,
                summary="no plan and no signal -- nothing to observe",
            )

        slos = derive_slos(plan)
        book = runbook(plan, slos)
        board = dashboard(plan, slos)
        ctx.state["slos"] = slos
        ctx.state["runbook"] = book
        ctx.state["dashboard"] = board
        return NodeResult(
            node="sre-observer",
            status=NodeStatus.ok,
            summary=f"derived {len(slos.objectives)} objective(s), {len(board['widgets'])} widget(s), 1 runbook",
            tokens=_tokens(book, slos.model_dump_json()),
            outputs={
                "slos": slos.model_dump(mode="json"),
                "dashboard": board,
                "runbook": book,
            },
        )

    return handler


# ---------------------------------------------------------------------------
# knowledge-curator
# ---------------------------------------------------------------------------


def knowledge_curator(deps: PipelineDeps) -> Any:
    def handler(ctx: AgentContext) -> NodeResult:
        recorded: list[dict[str, Any]] = []

        incident = ctx.payload.get("incident")
        if isinstance(incident, dict):
            memory = Memory(
                service=incident.get("service", ctx.state.get("plan").service if ctx.state.get("plan") else "unknown"),
                environment=incident.get("environment", ctx.state.get("environment", "dev")),
                symptom=incident["symptom"],
                root_cause=incident["root_cause"],
                guard=incident.get("guard", "none recorded"),
                lesson_id=incident.get("lesson_id"),
                request_id=ctx.request_id,
            )
            recorded.append(deps.learner.record(memory).model_dump(mode="json"))

        diagnosis = ctx.state.get("diagnosis")
        proposal = ctx.state.get("proposal")
        if diagnosis is not None and diagnosis.matched and proposal is not None:
            # An incident the SRE node classified is worth remembering even when
            # nobody filed one: the next build reads it back.
            memory = Memory(
                service=diagnosis.service,
                environment=diagnosis.environment,
                symptom=diagnosis.signal[:400],
                root_cause=f"{diagnosis.pattern_id}: {diagnosis.root_cause}",
                guard=proposal.action,
                lesson_id=diagnosis.pattern_id,
                request_id=ctx.request_id,
            )
            recorded.append(deps.learner.record(memory).model_dump(mode="json"))

        hits = ctx.state.get("lesson_hits", [])
        return NodeResult(
            node="knowledge-curator",
            status=NodeStatus.ok,
            summary=(
                f"recorded {len(recorded)} memory entr(y|ies); "
                f"{len(hits)} lesson note(s) carried into the report"
            ),
            tokens=_tokens(recorded),
            outputs={
                "recorded": recorded,
                "lesson_catalog": [lesson.id for lesson in deps.learner.catalog()],
            },
        )

    return handler


# ---------------------------------------------------------------------------
# release-executor
# ---------------------------------------------------------------------------


def release_executor(deps: PipelineDeps) -> Any:
    def handler(ctx: AgentContext) -> NodeResult:
        plan = ctx.state.get("plan")
        report = ctx.state.get("guard_report")
        if plan is None:
            # Reached directly via a `promote` intent, which routes past the
            # builder. Resolve and guard here rather than refusing: a promotion
            # packet for a plan nobody checked is worse than no packet.
            graph: ResourceGraph | None = ctx.state.get("graph")
            if graph is None:
                return NodeResult(
                    node="release-executor",
                    status=NodeStatus.blocked,
                    summary="no plan and no graph to promote",
                )
            try:
                plan = resolve(graph, deps.platform, deps.catalog, ctx.state.get("environment", "dev"))
            except GroundingError as e:
                return NodeResult(
                    node="release-executor",
                    status=NodeStatus.blocked,
                    summary=f"not grounded, so not promotable: {e}",
                )
            ctx.state["plan"] = plan
            report = evaluate(plan, deps.platform)
            ctx.state["guard_report"] = report
        if report is not None and report.blocked:
            return NodeResult(
                node="release-executor",
                status=NodeStatus.blocked,
                summary="the guard blocked this plan; promotion is not offered for a blocked plan",
            )

        env = deps.platform.environment(plan.environment)
        chain = [e.name for e in deps.platform.environments]
        index = chain.index(plan.environment)
        source = chain[index - 1] if index > 0 else None

        steps = [
            f"Confirm {source} deployed successfully within the last 24h" if source else
            "This is the first environment in the chain; no upstream check applies",
            "Re-run the guard against the target environment (production flips warnings to blocks)",
            f"Review the full diff for {plan.environment}",
            "Record the approval, then apply from your own terminal or pipeline",
        ]

        # This node never applies. It writes the change record a human executes.
        return NodeResult(
            node="release-executor",
            status=NodeStatus.ok,
            summary=(
                f"promotion packet for {plan.service} -> {plan.environment} "
                f"({'production' if env.production else 'non-production'}); "
                f"approvers: {', '.join(env.approvers) or 'none declared'}"
            ),
            tokens=_tokens(steps),
            outputs={
                "promotion": {
                    "service": plan.service,
                    "from_environment": source,
                    "to_environment": plan.environment,
                    "production": env.production,
                    "approvers": env.approvers,
                    "steps": steps,
                    "applied": False,
                    "note": (
                        "OpenCloudOps does not hold cloud credentials and does not apply. "
                        "This packet is the change record a human executes against."
                    ),
                }
            },
        )

    return handler


# ---------------------------------------------------------------------------
# factory
# ---------------------------------------------------------------------------


def build_runtime(
    spec: AgentGraphSpec,
    deps: PipelineDeps,
) -> AgentRuntime:
    """Wire every declared node to its handler.

    ``runtime.unhandled()`` is asserted empty by the test suite: a node declared
    in the graph with no handler would silently skip at run time, which looks
    like "the agent chose not to" rather than "nobody wrote it".
    """
    runtime = AgentRuntime(
        spec,
        audit=deps.audit,
        autonomous_up_to=deps.platform.autonomy.autonomous_up_to,
    )
    runtime.register("orchestrator")(orchestrator(deps))
    runtime.register("iac-builder")(iac_builder(deps))
    runtime.register("iac-validator")(iac_validator(deps))
    runtime.register("sre-observer")(sre_observer(deps))
    runtime.register("knowledge-curator")(knowledge_curator(deps))
    runtime.register("release-executor")(release_executor(deps))
    return runtime
