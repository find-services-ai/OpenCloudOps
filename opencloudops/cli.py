"""`occ` -- the OpenCloudOps command line.

Every command here runs the same agent graph the API and ChatOps run. There is
no "CLI shortcut" that bypasses grounding or the guard, because a bypass is
what people reach for under deadline pressure and it is exactly then that the
checks matter.

One deliberate asymmetry: a dry run (rendering to stdout or to a summary) is
granted the MEDIUM `iac-builder` gate automatically, because nothing is written
and nothing is applied. Writing files with `--out` requires `--approve
iac-builder`, and that is recorded in the audit log with the reason.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import typer
from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from opencloudops import __version__
from opencloudops.agentgraph import AgentGraphSpec, NodeStatus
from opencloudops.bootstrap.adapters import ADAPTER_NAMES
from opencloudops.agents import PipelineDeps, build_runtime
from opencloudops.audit import AuditLog
from opencloudops.chatops import ChatOps
from opencloudops.emitters import available
from opencloudops.graph import load_platform
from opencloudops.ground import PatternCatalog
from opencloudops.learner import Learner
from opencloudops import telemetry as otel

app = typer.Typer(
    add_completion=False,
    help="OpenCloudOps -- agentic cloud operations: ground, guard, generate, observe.",
)
console = Console()

DEFAULT_PLATFORM = Path(os.environ.get("OCC_PLATFORM", "platform/platform.yaml"))
DEFAULT_PATTERNS = Path(os.environ.get("OCC_PATTERNS", "patterns"))
DEFAULT_GRAPHS = Path(os.environ.get("OCC_GRAPHS", "graphs"))
DEFAULT_AGENT_GRAPH = Path(os.environ.get("OCC_AGENT_GRAPH", "agent-graph.yaml"))


def _deps() -> PipelineDeps:
    return PipelineDeps(
        platform=load_platform(DEFAULT_PLATFORM),
        catalog=PatternCatalog.from_dir(DEFAULT_PATTERNS),
        learner=Learner(),
        audit=AuditLog(),
        graphs_dir=DEFAULT_GRAPHS,
    )


def _spec() -> AgentGraphSpec:
    return AgentGraphSpec.load(DEFAULT_AGENT_GRAPH)


def _approvals(approve: list[str] | None, *, dry_run: bool) -> set[str]:
    granted = set(approve or [])
    if dry_run:
        # Recorded, not hidden: a dry run mutates nothing, so the MEDIUM gate on
        # code generation is satisfied by the absence of an effect.
        granted.add("iac-builder")
        granted.add("knowledge-curator")
    high = {
        name
        for name, node in _spec().nodes.items()
        if node.gate_tier and node.gate_tier.value == "HIGH"
    }
    refused = high & granted
    if refused:
        console.print(
            f"[red]Refusing --approve {', '.join(sorted(refused))}.[/red] A HIGH gate is not "
            "grantable by a flag; that is the whole point of the tier."
        )
        raise typer.Exit(2)
    return granted


def _report(run, *, verbose: bool = False) -> None:
    table = Table(title=f"agent graph — request {run.request_id[:16]}")
    for col in ("node", "status", "cycle", "tokens", "summary"):
        table.add_column(col, overflow="fold")
    for step in run.steps:
        style = {
            NodeStatus.ok: "green",
            NodeStatus.blocked: "red",
            NodeStatus.failed: "red",
            NodeStatus.awaiting_approval: "yellow",
            NodeStatus.skipped: "dim",
        }[step.status]
        table.add_row(
            step.node,
            f"[{style}]{step.status.value}[/{style}]",
            str(step.cycle),
            str(step.tokens),
            step.summary,
        )
    console.print(table)
    if run.escalation:
        console.print(f"[yellow]escalation:[/yellow] {run.escalation}")
    if verbose and run.trace_id:
        console.print(f"[dim]trace {run.trace_id}[/dim]")


@app.command()
def version() -> None:
    """Print the version and what this installation is wired to."""
    console.print(f"opencloudops {__version__}")
    console.print(f"  platform   {DEFAULT_PLATFORM}")
    console.print(f"  patterns   {DEFAULT_PATTERNS}")
    console.print(f"  graphs     {DEFAULT_GRAPHS}")
    console.print(f"  agent graph {DEFAULT_AGENT_GRAPH}")
    console.print(f"  backends   {', '.join(available())}")
    console.print(f"  tracing    {'on' if otel.configure() else 'off'}")


@app.command(name="list")
def list_things(
    what: str = typer.Argument("graphs", help="graphs | patterns | backends | lessons | nodes"),
) -> None:
    """Show what this installation knows about."""
    deps = _deps()
    if what == "graphs":
        for path in sorted(DEFAULT_GRAPHS.glob("*.graph.yaml")):
            console.print(f"  {path.name.removesuffix('.graph.yaml')}")
        return
    if what == "patterns":
        table = Table()
        for col in ("id", "node type", "provider", "status", "controls"):
            table.add_column(col, overflow="fold")
        for p in deps.catalog.all():
            table.add_row(p.id, p.node_type.value, p.provider.value, p.status.value, ", ".join(p.controls))
        console.print(table)
        return
    if what == "backends":
        for b in available():
            console.print(f"  {b}")
        return
    if what == "lessons":
        table = Table()
        for col in ("id", "severity", "pattern", "cost of deferral"):
            table.add_column(col, overflow="fold")
        for lesson in deps.learner.catalog():
            table.add_row(lesson.id, lesson.severity.value, lesson.pattern, lesson.cost_of_deferral)
        console.print(table)
        return
    if what == "nodes":
        spec = _spec()
        table = Table()
        for col in ("node", "role", "gate", "zone"):
            table.add_column(col, overflow="fold")
        for name, node in spec.nodes.items():
            table.add_row(name, node.role, node.risk_gate, ", ".join(node.zone))
        console.print(table)
        return
    console.print(f"[red]Unknown listing {what!r}.[/red] Try: graphs, patterns, backends, lessons, nodes")
    raise typer.Exit(1)


@app.command()
def plan(
    graph: str = typer.Argument(..., help="Graph name under graphs/, or a path."),
    env: str = typer.Option("dev", "--env"),
    ticket: str = typer.Option(None, "--ticket"),
    json_out: Path = typer.Option(None, "--json", help="Write the full run to this path."),
) -> None:
    """Ground a graph, guard it, and show what would be built."""
    payload = {"graph": graph, "environment": env, "languages": ["terraform"]}
    if ticket:
        payload["ticket"] = ticket
    run = build_runtime(_spec(), _deps()).run("plan", payload, approvals=_approvals(None, dry_run=True))
    _report(run)
    resolved = run.output("plan")
    if resolved:
        console.print(f"\n[bold]{len(resolved['nodes'])} node(s)[/bold] in apply order:")
        for node in resolved["nodes"]:
            console.print(f"  {node['id']:16s} {node['type']:28s} {node['name']}")
    if json_out:
        json_out.write_text(run.model_dump_json(indent=2) + "\n")
        console.print(f"[dim]wrote {json_out}[/dim]")
    raise typer.Exit(0 if run.status is NodeStatus.ok else 1)


@app.command()
def iac(
    graph: str = typer.Argument(...),
    env: str = typer.Option("dev", "--env"),
    language: str = typer.Option("terraform", "--language", "-l", help=f"One of: {', '.join(available())}"),
    out: Path = typer.Option(None, "--out", help="Write files here. Requires --approve iac-builder."),
    approve: list[str] = typer.Option(None, "--approve", help="Grant a gated node for this invocation."),
    show: str = typer.Option(None, "--show", help="Print one emitted file by name."),
) -> None:
    """Emit infrastructure-as-code for a graph."""
    languages = [part.strip() for part in language.split(",")]
    payload = {"graph": graph, "environment": env, "languages": languages}
    run = build_runtime(_spec(), _deps()).run(
        "plan", payload, approvals=_approvals(approve, dry_run=out is None)
    )
    _report(run)
    if run.status is not NodeStatus.ok:
        raise typer.Exit(1)

    from opencloudops.emitters import get_emitter
    from opencloudops.spec import ResolvedPlan

    resolved = ResolvedPlan.model_validate(run.output("plan"))
    deps = _deps()
    written = 0
    for lang in languages:
        files = get_emitter(lang).emit(resolved, deps.platform)
        if show:
            match = next((k for k in files if k.endswith(show)), None)
            if match:
                lexer = {"tf": "hcl", "yaml": "yaml", "py": "python", "ts": "typescript", "json": "json"}
                console.print(Syntax(files[match], lexer.get(match.rsplit(".", 1)[-1], "text"), line_numbers=False))
                continue
        if out:
            for rel, content in files.items():
                path = out / lang / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                written += 1
        else:
            console.print(f"\n[bold]{lang}[/bold] — {len(files)} file(s)")
            for rel in sorted(files):
                console.print(f"  {rel:44s} {len(files[rel]):7d} bytes")
    if out:
        console.print(f"\n[green]wrote {written} file(s) to {out}[/green]")
    elif not show:
        console.print("\n[dim]Dry run. Pass --out DIR --approve iac-builder to write these.[/dim]")


@app.command()
def guard(
    graph: str = typer.Argument(...),
    env: str = typer.Option("dev", "--env"),
) -> None:
    """Run the security baseline against a graph without emitting anything."""
    run = build_runtime(_spec(), _deps()).run(
        "validate", {"graph": graph, "environment": env}, approvals=_approvals(None, dry_run=True)
    )
    report = run.output("guard") or {}
    findings = report.get("findings", [])
    table = Table(title=f"guard — {graph} ({env}) — {report.get('checks_run', 0)} checks")
    for col in ("severity", "rule", "node", "message", "remediation"):
        table.add_column(col, overflow="fold")
    for f in findings:
        style = {"BLOCK": "red", "WARN": "yellow", "INFO": "dim"}[f["severity"]]
        table.add_row(
            f"[{style}]{f['severity']}[/{style}]",
            f["rule_id"],
            f.get("node_id") or "—",
            f["message"],
            f.get("remediation", ""),
        )
    console.print(table)
    console.print(f"risk tier: [bold]{run.output('risk_tier')}[/bold]")
    raise typer.Exit(1 if any(f["severity"] == "BLOCK" for f in findings) else 0)


@app.command()
def slo(
    graph: str = typer.Argument(...),
    env: str = typer.Option("dev", "--env"),
    runbook_out: Path = typer.Option(None, "--runbook", help="Write the runbook here."),
) -> None:
    """Derive SLOs, error budgets and burn-rate alerts, and optionally the runbook."""
    run = build_runtime(_spec(), _deps()).run(
        "plan", {"graph": graph, "environment": env}, approvals=_approvals(None, dry_run=True)
    )
    slos = run.output("slos") or {}
    table = Table(title=f"SLOs — {graph} ({env})")
    for col in ("node", "objective", "error budget", "p95", "indicator"):
        table.add_column(col, overflow="fold")
    for o in slos.get("objectives", []):
        table.add_row(
            o["node_id"],
            f"{o['objective_pct']}%",
            f"{o['error_budget_minutes']} min / {o['window_days']}d",
            f"{o['latency_p95_ms']}ms" if o.get("latency_p95_ms") else "—",
            o["indicator"],
        )
    console.print(table)
    if runbook_out:
        runbook_out.write_text(run.output("runbook") or "", encoding="utf-8")
        console.print(f"[dim]wrote {runbook_out}[/dim]")


@app.command()
def diagnose(
    service: str = typer.Argument(...),
    signal: str = typer.Option(..., "--signal", help="The error text, log line, or alarm body."),
    env: str = typer.Option("prod", "--env"),
    remediate: bool = typer.Option(False, "--remediate", help="Also propose a fix."),
) -> None:
    """Classify a production signal against the error-pattern library."""
    run = build_runtime(_spec(), _deps()).run(
        "triage",
        {"service": service, "signal": signal, "environment": env},
        approvals=_approvals(None, dry_run=True),
    )
    d = run.output("diagnosis") or {}
    console.print(f"[bold]{d.get('summary', 'no diagnosis')}[/bold]")
    console.print(f"  pattern    {d.get('pattern_id') or 'unrecognised'}")
    console.print(f"  confidence {d.get('confidence')}")
    console.print(f"  root cause {d.get('root_cause')}")
    if d.get("evidence"):
        console.print(f"  matched    {', '.join(d['evidence'])}")
    for step in d.get("next_steps", []):
        console.print(f"  → {step}")
    if remediate:
        p = run.output("proposal") or {}
        console.print("\n[bold]Proposal[/bold]")
        console.print(f"  action  {p.get('action')}")
        console.print(f"  change  {p.get('change')}")
        console.print(f"  verify  {p.get('verification')}")
        verdict = "an agent may apply this" if p.get("autonomous") else "needs a human"
        console.print(f"  risk    {p.get('risk_tier')} — {verdict}")
        if p.get("blocked_reason"):
            console.print(f"  because {p['blocked_reason']}")


@app.command()
def promote(
    graph: str = typer.Argument(...),
    env: str = typer.Option(..., "--env", help="Target environment."),
) -> None:
    """Build the change record for promoting a graph. Never applies anything."""
    run = build_runtime(_spec(), _deps()).run(
        "promote",
        {"graph": graph, "environment": env},
        approvals=_approvals(["release-executor"] if _spec().nodes["release-executor"].gate_tier
                             and _spec().nodes["release-executor"].gate_tier.value != "HIGH" else None,
                             dry_run=True),
    )
    _report(run)
    promo = run.output("promotion")
    if not promo:
        console.print("[yellow]No promotion packet produced.[/yellow]")
        raise typer.Exit(1)
    console.print(f"\n[bold]{promo['service']} → {promo['to_environment']}[/bold]")
    console.print(f"  from       {promo['from_environment'] or 'n/a'}")
    console.print(f"  production {promo['production']}")
    console.print(f"  approvers  {', '.join(promo['approvers']) or 'none declared'}")
    for i, step in enumerate(promo["steps"], 1):
        console.print(f"  {i}. {step}")
    console.print(f"\n[dim]{promo['note']}[/dim]")


@app.command()
def learn(
    service: str = typer.Option(..., "--service"),
    symptom: str = typer.Option(..., "--symptom"),
    cause: str = typer.Option(..., "--cause"),
    guard_note: str = typer.Option("none recorded", "--guard", help="What now prevents a recurrence."),
    env: str = typer.Option("prod", "--env"),
) -> None:
    """Record an incident so the next build starts from it."""
    run = build_runtime(_spec(), _deps()).run(
        "learn",
        {
            "environment": env,
            "service": service,
            "incident": {
                "service": service,
                "environment": env,
                "symptom": symptom,
                "root_cause": cause,
                "guard": guard_note,
            },
        },
        approvals=_approvals(None, dry_run=True),
    )
    recorded = run.output("recorded") or []
    console.print(f"[green]recorded {len(recorded)} memory entry(ies)[/green]")


@app.command()
def chat(
    command: list[str] = typer.Argument(None, help="A ChatOps command. Omit for an interactive session."),
) -> None:
    """Run a ChatOps command, or open an interactive session."""
    session = ChatOps(_deps(), _spec())
    if command:
        console.print(session.handle(" ".join(command)).text)
        return
    console.print("[bold]OpenCloudOps ChatOps.[/bold] Type `help`, or `exit` to leave.\n")
    while True:
        try:
            line = console.input("[cyan]occ>[/cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return
        if line in {"exit", "quit"}:
            return
        if not line:
            continue
        console.print(session.handle(line).text + "\n")


@app.command()
def logs(last: int = typer.Option(20, "--last")) -> None:
    """Tail the audit trail."""
    for event in AuditLog().tail(last):
        console.print(json.dumps(event))


@app.command()
def bootstrap(
    target: Path = typer.Argument(..., help="Repository to scaffold OpenCloudOps into."),
    agent: str = typer.Option("agnostic", "--agent", help="One of: " + ", ".join(ADAPTER_NAMES)),
    overwrite: bool = typer.Option(False, "--overwrite", help="Replace files that already exist in target."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print what would be written; write nothing."),
) -> None:
    """Scaffold this platform contract into another repo for one coding tool.

    Writes AGENTS.md plus files in that tool's own format (CLAUDE.md +
    .claude/commands/ for claude-code, .cursor/rules/ for cursor,
    .github/agents/ for copilot, .continue/ for continue) -- all wired to
    the occ CLI, none of it granting an apply capability that doesn't exist.

    Every tool gets one file per node in agent-graph.yaml: the graph is the
    source of truth, not a hand-authored copy of it.
    """
    from opencloudops.bootstrap.sdk import Bootstrap

    if agent not in ADAPTER_NAMES:
        console.print(f"[red]Unknown agent {agent!r}.[/red] Choose from: {', '.join(ADAPTER_NAMES)}")
        raise typer.Exit(2)

    b = Bootstrap(_deps().platform, target_dir=target, agent=agent, overwrite=overwrite)

    if dry_run:
        for path, content in sorted(b.dry_run().items()):
            console.print(f"\n[bold]{path}[/bold] ({len(content)} bytes)")
        return

    conflicts = b.validate()
    if conflicts and not overwrite:
        console.print(f"[red]{len(conflicts)} file(s) already exist in {target}:[/red]")
        for c in conflicts:
            console.print(f"  {c}")
        console.print("[dim]Pass --overwrite to replace them, or --dry-run to preview first.[/dim]")
        raise typer.Exit(1)

    written = b.run()
    console.print(f"[green]Wrote {len(written)} file(s) to {target}[/green] (agent: {agent})")
    for path in written:
        console.print(f"  {path}")


@app.command(name="validate-graph")
def validate_graph(
    path: Path = typer.Argument(Path("agent-graph.yaml"), help="Graph file to validate."),
) -> None:
    """Check agent-graph.yaml against structure (Pydantic) and safety
    invariants (bootstrap.graph_invariants) -- the same two checks a bad
    graph would fail at load time or at scaffold time, run standalone so a
    hand edit can be checked before either."""
    from opencloudops.bootstrap.graph_invariants import graph_invariant_errors

    try:
        spec = AgentGraphSpec.load(path)
    except Exception as e:
        console.print(f"[red]Structurally invalid:[/red] {e}")
        raise typer.Exit(1) from None

    errors = graph_invariant_errors(spec)
    if errors:
        console.print(f"[red]{len(errors)} safety invariant violation(s) in {path}:[/red]")
        for e in errors:
            console.print(f"  - {e}")
        raise typer.Exit(1)

    console.print(f"[green]Valid.[/green] {path} -- {len(spec.nodes)} nodes, structure and safety invariants both pass.")


def main() -> None:
    otel.configure()
    app()


if __name__ == "__main__":
    main()
