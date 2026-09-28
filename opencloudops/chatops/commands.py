"""Deterministic ChatOps command plane.

Grammar, in full:

    <verb> [target] [--flag value] [--flag "quoted value"]

That is the whole language. It is small on purpose: every command maps onto
exactly one agent-graph intent, so what someone types in chat and what the
runtime executes cannot drift apart.

No LLM parses these. The approval verb in particular has to be unambiguous --
"approve iac-builder" grants exactly one node, for exactly one session.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from opencloudops.agentgraph import AgentGraphSpec, AgentRun, NodeStatus
from opencloudops.agents import PipelineDeps, build_runtime
from opencloudops.emitters import available
from opencloudops.ground import PatternCatalog
from opencloudops.learner import JUNCTION_QUESTIONS, Memory
from opencloudops.spec import PlatformContract


class Command(BaseModel):
    verb: str
    target: str = ""
    options: dict[str, str] = Field(default_factory=dict)
    raw: str = ""


class ChatReply(BaseModel):
    text: str
    request_id: str | None = None
    trace_id: str | None = None
    status: str = "ok"
    data: dict[str, Any] = Field(default_factory=dict)


class CommandError(ValueError):
    """Raised for input the grammar does not accept."""


def parse(text: str) -> Command:
    raw = text.strip()
    if raw.startswith("/"):
        raw = raw[1:]
    if raw.lower().startswith("occ "):
        raw = raw[4:]
    if not raw.strip():
        raise CommandError("empty command. Try `help`.")

    try:
        tokens = shlex.split(raw)
    except ValueError as e:
        raise CommandError(f"could not parse (unbalanced quote?): {e}") from None

    verb = tokens[0].lower()
    target = ""
    options: dict[str, str] = {}
    i = 1
    if i < len(tokens) and not tokens[i].startswith("--"):
        target = tokens[i]
        i += 1
    while i < len(tokens):
        token = tokens[i]
        if not token.startswith("--"):
            raise CommandError(
                f"unexpected argument {token!r}. Options must be written --name value."
            )
        name = token[2:]
        if "=" in name:
            name, value = name.split("=", 1)
            options[name] = value
            i += 1
            continue
        if i + 1 < len(tokens) and not tokens[i + 1].startswith("--"):
            options[name] = tokens[i + 1]
            i += 2
        else:
            options[name] = "true"
            i += 1
    return Command(verb=verb, target=target, options=options, raw=text.strip())


#: verb -> agent-graph intent. A verb with no entry here is handled locally
#: (help, listings) and never starts an agent run.
_INTENTS = {
    "plan": "plan",
    "iac": "plan",
    "build": "plan",
    "guard": "validate",
    "validate": "validate",
    "slo": "plan",
    "runbook": "plan",
    "diagnose": "triage",
    "remediate": "triage",
    "promote": "promote",
    "learn": "learn",
}

HELP = """**OpenCloudOps ChatOps**

`plan <graph> [--env dev] [--ticket ABC-1]`  ground, guard and cost a graph
`iac <graph> [--env dev] [--lang terraform] [--write out/]`  emit infrastructure code
`guard <graph> [--env prod]`  run the security baseline only
`slo <graph> [--env dev]`  objectives, error budgets and burn-rate alerts
`runbook <graph> [--env dev]`  the on-call document for this service
`diagnose <service> --signal "<error text>" [--env prod]`  classify a production signal
`remediate <service> --signal "<error text>" [--env prod]`  propose a fix with its risk tier
`promote <graph> --env staging`  build the change record a human applies
`learn --service s --symptom "..." --cause "..." --guard "..."`  record an incident

`graphs` · `patterns` · `backends` · `lessons` · `questions` · `approvals` · `status`
`approve <node>`  grant one gated node for this session
`help`  this message

Approvals last for this session only, and never cover `release-executor`."""


class ChatOps:
    """A stateful chat session over the agent graph.

    State is one set: which gated nodes the operator has approved. It is
    per-session and never persisted, so an approval cannot outlive the
    conversation that granted it.
    """

    def __init__(
        self,
        deps: PipelineDeps,
        spec: AgentGraphSpec,
        *,
        out_dir: Path | str = "out",
    ) -> None:
        self.deps = deps
        self.spec = spec
        self.out_dir = Path(out_dir)
        self.approvals: set[str] = set()
        self.last_run: AgentRun | None = None

    # -- entry point ----------------------------------------------------

    def handle(self, text: str) -> ChatReply:
        try:
            command = parse(text)
        except CommandError as e:
            return ChatReply(text=f"{e}\n\nType `help` for the command list.", status="error")

        local = {
            "help": self._help,
            "graphs": self._graphs,
            "patterns": self._patterns,
            "backends": self._backends,
            "lessons": self._lessons,
            "questions": self._questions,
            "approvals": self._approvals,
            "approve": self._approve,
            "status": self._status,
        }
        if command.verb in local:
            return local[command.verb](command)

        if command.verb not in _INTENTS:
            return ChatReply(
                text=f"Unknown command `{command.verb}`. Type `help`.", status="error"
            )
        return self._run(command)

    # -- local commands -------------------------------------------------

    def _help(self, _: Command) -> ChatReply:
        return ChatReply(text=HELP)

    def _graphs(self, _: Command) -> ChatReply:
        paths = sorted(self.deps.graphs_dir.glob("*.graph.yaml"))
        if not paths:
            return ChatReply(text=f"No graphs in `{self.deps.graphs_dir}/`.")
        lines = ["**Resource graphs**", ""]
        lines += [f"- `{p.name.removesuffix('.graph.yaml')}`" for p in paths]
        return ChatReply(text="\n".join(lines), data={"graphs": [p.name for p in paths]})

    def _patterns(self, _: Command) -> ChatReply:
        lines = ["**Approved patterns** (the only infrastructure agents may emit)", ""]
        for p in self.deps.catalog.all():
            lines.append(f"- `{p.id}` — {p.node_type.value} on {p.provider.value} [{p.status.value}]")
        return ChatReply(text="\n".join(lines))

    def _backends(self, _: Command) -> ChatReply:
        return ChatReply(
            text="**IaC backends**\n\n" + "\n".join(f"- `{b}`" for b in available()),
            data={"backends": available()},
        )

    def _lessons(self, _: Command) -> ChatReply:
        lines = ["**Production lessons enforced on every build**", ""]
        for lesson in self.deps.learner.catalog():
            lines.append(f"- `{lesson.id}` [{lesson.severity.value}] — {lesson.pattern}")
        return ChatReply(text="\n".join(lines))

    def _questions(self, _: Command) -> ChatReply:
        lines = ["**Junction questions** — answering these early avoids rework", ""]
        for q in JUNCTION_QUESTIONS:
            lines.append(f"- **{q['question']}**\n  sets `{q['sets']}` — {q['why']}")
        return ChatReply(text="\n".join(lines))

    def _approvals(self, _: Command) -> ChatReply:
        gated = {
            name: node.risk_gate
            for name, node in self.spec.nodes.items()
            if node.gate_tier is not None
        }
        lines = ["**Gated nodes**", ""]
        for name, tier in sorted(gated.items()):
            state = "approved this session" if name in self.approvals else "not approved"
            if tier.upper() == "HIGH":
                state = "human only — cannot be approved from chat"
            lines.append(f"- `{name}` ({tier}) — {state}")
        return ChatReply(text="\n".join(lines), data={"approved": sorted(self.approvals)})

    def _approve(self, command: Command) -> ChatReply:
        node = command.target
        if not node:
            return ChatReply(text="Usage: `approve <node>`. See `approvals`.", status="error")
        if node not in self.spec.nodes:
            return ChatReply(text=f"No node `{node}` in the agent graph.", status="error")
        tier = self.spec.nodes[node].gate_tier
        if tier is None:
            return ChatReply(text=f"`{node}` is not gated; nothing to approve.")
        if tier.value == "HIGH":
            # The one refusal in the command plane, and it is not configurable.
            return ChatReply(
                text=(
                    f"`{node}` is gated HIGH. A HIGH gate is never granted from chat — "
                    "it exists so a person applies the change themselves, from their own "
                    "terminal, having read the diff."
                ),
                status="refused",
            )
        self.approvals.add(node)
        return ChatReply(
            text=f"Approved `{node}` ({tier.value}) for this session only.",
            data={"approved": sorted(self.approvals)},
        )

    def _status(self, _: Command) -> ChatReply:
        if self.last_run is None:
            return ChatReply(text="No run in this session yet.")
        run = self.last_run
        lines = [
            f"**Last run** `{run.request_id}`",
            "",
            f"- intent: `{run.intent}`",
            f"- status: **{run.status.value}**",
            f"- risk tier: {run.risk_tier.value}",
            f"- tokens: {run.tokens}",
            "",
            "| node | status | cycle | summary |",
            "|---|---|---|---|",
        ]
        for step in run.steps:
            lines.append(
                f"| `{step.node}` | {step.status.value} | {step.cycle} | {step.summary} |"
            )
        return ChatReply(text="\n".join(lines), request_id=run.request_id, trace_id=run.trace_id)

    # -- agent-backed commands ------------------------------------------

    def _payload(self, command: Command) -> dict[str, Any]:
        opts = command.options
        payload: dict[str, Any] = {
            "environment": opts.get("env", "dev"),
        }
        if command.verb in {"diagnose", "remediate"}:
            payload["service"] = command.target
            payload["signal"] = opts.get("signal", "")
        elif command.verb == "learn":
            payload["incident"] = {
                "service": opts.get("service", command.target or "unknown"),
                "environment": opts.get("env", "dev"),
                "symptom": opts.get("symptom", ""),
                "root_cause": opts.get("cause", ""),
                "guard": opts.get("guard", "none recorded"),
            }
        else:
            payload["graph"] = command.target
        if "ticket" in opts:
            payload["ticket"] = opts["ticket"]
        languages = opts.get("lang") or opts.get("language")
        payload["languages"] = [l.strip() for l in languages.split(",")] if languages else ["terraform"]
        return payload

    def _run(self, command: Command) -> ChatReply:
        verb = command.verb
        if verb in {"diagnose", "remediate"} and not command.options.get("signal"):
            return ChatReply(
                text=f'Usage: `{verb} <service> --signal "<error text>"`.', status="error"
            )
        if verb not in {"diagnose", "remediate", "learn"} and not command.target:
            return ChatReply(text=f"Usage: `{verb} <graph> [--env dev]`.", status="error")

        payload = self._payload(command)
        runtime = build_runtime(self.spec, self.deps)
        try:
            run = runtime.run(_INTENTS[verb], payload, approvals=set(self.approvals))
        except Exception as e:
            return ChatReply(text=f"Run failed: {type(e).__name__}: {e}", status="error")
        self.last_run = run

        rendered = self._render(verb, command, run)
        return ChatReply(
            text=rendered,
            request_id=run.request_id,
            trace_id=run.trace_id,
            status=run.status.value,
            data={"steps": [s.model_dump(mode="json") for s in run.steps]},
        )

    def _render(self, verb: str, command: Command, run: AgentRun) -> str:
        head = [f"**{verb}** `{command.target or command.options.get('service', '')}`"]
        if run.status is NodeStatus.awaiting_approval:
            nodes = ", ".join(f"`{n}`" for n in run.approvals_required)
            return "\n".join(
                head
                + [
                    "",
                    f"Held at a gate. {nodes} needs an approval before this can continue.",
                    "",
                    f"Approve it for this session with `approve {run.approvals_required[0]}`, "
                    "then re-run the command.",
                ]
            )
        if run.status is NodeStatus.blocked:
            return "\n".join(
                head + ["", f"**Blocked.** {run.escalation or self._last_summary(run)}"]
            )
        if run.status is NodeStatus.failed:
            return "\n".join(head + ["", f"**Failed.** {run.escalation or self._last_summary(run)}"])

        body = self._success_body(verb, command, run)
        trace = f"\n\n_request `{run.request_id}` · {len(run.steps)} node(s) · {run.tokens} tokens_"
        return "\n".join(head + ["", body]) + trace

    def _last_summary(self, run: AgentRun) -> str:
        return run.steps[-1].summary if run.steps else "no steps ran"

    def _success_body(self, verb: str, command: Command, run: AgentRun) -> str:
        if verb in {"diagnose", "remediate"}:
            d = run.output("diagnosis") or {}
            p = run.output("proposal") or {}
            if not d:
                return "No diagnosis was produced."
            lines = [
                f"**{d.get('summary') or 'Unrecognised signal'}**",
                "",
                f"- pattern: `{d.get('pattern_id') or 'none'}` at {d.get('confidence')} confidence",
                f"- root cause: {d.get('root_cause')}",
            ]
            if d.get("evidence"):
                lines.append(f"- matched: {', '.join('`' + e + '`' for e in d['evidence'])}")
            if d.get("affected_nodes"):
                lines.append(f"- affected: {', '.join('`' + n + '`' for n in d['affected_nodes'])}")
            if verb == "remediate" and p:
                lines += [
                    "",
                    f"**Proposal:** {p.get('action')}",
                    f"- change: `{p.get('change')}`",
                    f"- verify: {p.get('verification')}",
                    f"- risk: **{p.get('risk_tier')}** — "
                    + ("an agent may apply this" if p.get("autonomous") else "needs a human"),
                ]
                if p.get("blocked_reason"):
                    lines.append(f"- why not autonomous: {p['blocked_reason']}")
                if p.get("approvers"):
                    lines.append(f"- approvers: {', '.join(p['approvers'])}")
            else:
                lines += ["", "**Next steps**"] + [f"- {s}" for s in d.get("next_steps", [])]
            return "\n".join(lines)

        if verb == "learn":
            recorded = run.output("recorded") or []
            return f"Recorded {len(recorded)} memory entry(ies). The next build recalls them."

        if verb == "promote":
            promo = run.output("promotion")
            if not promo:
                return "No promotion packet was produced."
            lines = [
                f"**Change record: {promo['service']} → {promo['to_environment']}**",
                "",
                f"- from: `{promo['from_environment'] or 'n/a'}`",
                f"- production: {promo['production']}",
                f"- approvers: {', '.join(promo['approvers']) or 'none declared'}",
                "",
                "**Steps**",
            ]
            lines += [f"{i}. {s}" for i, s in enumerate(promo["steps"], 1)]
            lines += ["", f"_{promo['note']}_"]
            return "\n".join(lines)

        # plan / iac / guard / slo / runbook all come from the same run.
        guard = run.output("guard") or {}
        plan = run.output("plan") or {}
        lines: list[str] = []

        if verb == "runbook":
            return run.output("runbook") or "No runbook was derived."

        if verb == "slo":
            slos = run.output("slos") or {}
            objectives = slos.get("objectives", [])
            if not objectives:
                return "No SLO-bearing nodes in this plan."
            lines = ["| node | objective | error budget | p95 |", "|---|---|---|---|"]
            for o in objectives:
                latency = f"{o['latency_p95_ms']}ms" if o.get("latency_p95_ms") else "—"
                lines.append(
                    f"| `{o['node_id']}` | {o['objective_pct']}% | "
                    f"{o['error_budget_minutes']} min / {o['window_days']}d | {latency} |"
                )
            lines += [
                "",
                "Each objective carries a fast-burn page (14.4x over 1h) and a slow-burn "
                "ticket (3x over 6h).",
            ]
            return "\n".join(lines)

        nodes = plan.get("nodes", [])
        lines.append(
            f"Grounded **{len(nodes)}** node(s) for `{plan.get('environment')}` "
            f"in `{plan.get('region')}`."
        )
        repairs = run.output("repairs") or []
        if repairs:
            lines += ["", "**Repaired automatically**"]
            lines += [f"- `{r['node_id']}` {r['rule_id']}: {r['before']} → {r['after']}" for r in repairs]
        if guard:
            findings = guard.get("findings", [])
            warns = [f for f in findings if f["severity"] == "WARN"]
            lines += [
                "",
                f"Guard: **{len(guard.get('findings', []))} finding(s)** across "
                f"{guard.get('checks_run')} checks; risk tier **{run.output('risk_tier')}**.",
            ]
            for f in warns[:5]:
                lines.append(f"- WARN `{f['rule_id']}` {f.get('node_id') or ''}: {f['message']}")

        if verb == "iac":
            paths = run.output("artifact_paths") or {}
            lines += ["", "**Emitted**"]
            for language, files in paths.items():
                lines.append(f"- `{language}`: {len(files)} file(s) — {', '.join(files[:4])}"
                             + (" …" if len(files) > 4 else ""))
            written = self._maybe_write(command, run)
            if written:
                lines += ["", written]
        else:
            mermaid = run.output("mermaid")
            if mermaid:
                lines += ["", "```mermaid", mermaid, "```"]
        return "\n".join(lines)

    def _maybe_write(self, command: Command, run: AgentRun) -> str:
        """`--write` is the one command that touches the filesystem.

        It writes only under the configured output directory, and only files the
        emitters produced in this run.
        """
        target = command.options.get("write")
        if not target:
            return ""
        if "iac-builder" not in self.approvals:
            return (
                "_Not written: `--write` puts files on disk, which is the MEDIUM-gated part. "
                "Run `approve iac-builder` first._"
            )
        plan = run.output("plan") or {}
        base = (self.out_dir / target).resolve()
        root = self.out_dir.resolve()
        if not str(base).startswith(str(root)):
            return "_Not written: `--write` may only target a path inside the output directory._"

        # Re-render from the already-resolved plan rather than re-running the
        # graph: the run that produced this plan passed the guard, and a second
        # run could legitimately produce different output.
        from opencloudops.emitters import get_emitter
        from opencloudops.spec import ResolvedPlan

        resolved = ResolvedPlan.model_validate(plan)
        count = 0
        for language in (run.output("artifact_paths") or {}):
            files = get_emitter(language).emit(resolved, self.deps.platform)
            for rel, content in files.items():
                path = base / language / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                count += 1
        return f"_Wrote {count} file(s) to `{base}`._"
