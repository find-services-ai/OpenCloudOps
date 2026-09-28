# Architecture

This document explains the design, and — more importantly — why the
boundaries are where they are. If you're extending this framework, the
"why" is what tells you whether your change belongs inside a boundary or
needs a new one.

## The three documents that constrain every agent

Everything an agent can do traces back to three YAML documents. An agent
that wants to do something not expressible by combining these three cannot
do it — that's the whole point of grounding and guarding.

1. **`platform/platform.yaml`** — the platform contract. Org, business unit,
   environments, the naming template, mandatory tags, the security baseline,
   and the autonomy ladder. One organization, one file.
2. **`*.graph.yaml`** — a resource graph. An application's shape, expressed
   as provider-neutral node types (`compute.container_service`,
   `store.relational`, ...) and edges (`depends_on`, `reads`, `writes`,
   `publishes`, `consumes`). Human-authored, or produced by
   `opencloudops/integrations/ideas.py` from an existing repository.
3. **`patterns/*.yaml`** — the pattern catalog. The *only* concrete
   infrastructure the framework may emit. Each pattern names its provider,
   its node type, the security `controls` it guarantees, and the SLO
   defaults a service built from it starts with.

`opencloudops/spec.py` is the typed contract for all three (Pydantic
models with validators — a malformed environment, an ungrounded node type,
a naming template missing a required token, all fail at load time with a
specific message, not three steps later as a mysterious KeyError).

## Ground → name → tag → guard → emit

`opencloudops/graph.py::resolve()` turns a `ResourceGraph` plus a
`PlatformContract` plus a `PatternCatalog` into a `ResolvedPlan`: every node
grounded in exactly one pattern (`ground.py`), named by the platform's
naming template (`naming.py`), tagged with every mandatory tag, and ordered
by dependency (`Relation.depends_on`/`reads`/`writes`/`invokes` constrain
apply order; `publishes`/`consumes` describe data flow and don't).

Emitters (`opencloudops/emitters/*.py`) receive only the `ResolvedPlan`.
They never pick a resource, never compute a name, never relax a control —
they render. That split is what makes five backends maintainable: a naming
change or a new required control is one edit in the core, not five edits in
five templates. Each emitter that can't express every node type
(Kubernetes has no VPC or RDS projection) writes an explicit
`UNSUPPORTED.md` naming what it left out — silence there would let a
reviewer believe coverage was complete.

`opencloudops/guard.py::evaluate()` runs the security baseline against the
resolved plan. A `BLOCK` finding is checked by the agent runtime, not just
reported: `iac-validator` refuses to hand a blocked plan downstream. Every
field on `SecurityBaseline` has a check behind it — enforced by
`tests/test_guard.py`'s coverage test, so a baseline setting can't become a
promise the framework silently drops.

`opencloudops/learner.py` runs a second pass over the *emitted text* — the
lessons a pattern-level check can't see, like a hardcoded `count =
length(...)` or one environment's identifier leaking into another
environment's file. Each lesson from the framework this project's ideas
came from now has an executable detector; a lesson with no detector can't
be registered.

## The agent graph: engineering, not a pipeline

`agent-graph.yaml` declares the six nodes, the edges between them, and the
policy each edge and node carries. `opencloudops/agentgraph.py` is the
runtime that executes that declaration — it hardcodes no pipeline. Changing
behavior is an edit to the YAML.

- **Zone defence.** Each node owns a disjoint set of concerns
  (`iac-builder` owns `resource-graph, naming, iac-emission`; `sre-observer`
  owns `slo-derivation, alerting, ...`). Two nodes claiming the same zone
  fails to load — overlapping ownership is how two agents end up fighting
  over one file.
- **Gates.** A node's `risk_gate` (`none | LOW | MEDIUM | HIGH`) decides
  whether it needs a recorded approval before running. `iac-builder` and
  `release-executor` are `MEDIUM`; nothing ships `HIGH`, because nothing in
  this framework applies anything — the API and ChatOps planes refuse to
  grant `HIGH` *categorically* (by tier, not by node name), so a future
  apply capability can't be switched on quietly.
- **Bounded repair.** The `iac-validator → iac-builder` edge carries
  `max_cycles: 3`. When the validator's finding has exactly one correct fix
  (`opencloudops/repair.py` — a single-AZ production database should be
  Multi-AZ; nothing else is repaired automatically, including a CIDR or a
  public-exposure flag, both of which are irreducibly a human decision), it
  mutates a *copy* of the graph and hands it back. Three cycles, then
  escalate — an unbounded fix loop is how an agent burns an afternoon
  converging on nothing.
- **Error decontamination.** On a node failure, its local `scratch` state is
  dropped before a retry — reusing the context that caused a failure is how
  three retries fail identically.
- **Token budgets.** `evals.per_node_hard` / `per_run_hard` are enforced,
  not aspirational; a node that blows its ceiling stops the run rather than
  quietly costing more than intended.
- **OpenTelemetry throughout.** Every node execution is a span
  (`opencloudops/telemetry.py`); a run's `request_id` *is* the trace id when
  a collector is configured, so a ChatOps reply names something you can
  paste into Jaeger.

## The SRE plane: setup and operation

`opencloudops/sre/slo.py` derives an availability objective, an error
budget, and a two-window burn-rate alert (14.4x/1h → page, 3x/6h → ticket —
the multi-window approach from the Google SRE workbook) from each pattern's
declared SLO defaults, and writes the runbook every alert annotation points
at. Nothing here is invented at incident time; it's fixed when the pattern
is chosen.

`opencloudops/sre/patterns.py` is a library of known failure shapes, each
with a `confidence` (how sure the classifier is) and an *independent*
`auto_remediable` flag. Those are different questions: a database
connection failure during a failover looks identical to one during an
outage, so it's `never_retry` regardless of confidence.
`opencloudops/sre/remediate.py::propose()` requires three things to agree
before returning `autonomous: true` — the pattern allows it, the risk tier
is within the platform's autonomy ceiling, *and* the environment isn't
production — with production excluded unconditionally rather than via the
ladder, so a one-line config change can't hand an agent production.

## Three surfaces, one graph

The CLI (`opencloudops/cli.py`), the API (`opencloudops/api.py`), and
ChatOps (`opencloudops/chatops/commands.py`) all build the same
`AgentRuntime` from the same `agent-graph.yaml` and the same handlers
(`opencloudops/agents/handlers.py`). There's no "CLI shortcut" that skips
grounding or the guard — that's exactly the shortcut people reach for under
deadline pressure, and it's exactly then the checks matter.

ChatOps's command grammar is deliberately small and has no model in the
parsing path: `<verb> [target] [--flag value]`. The one place that would be
catastrophic to get probabilistically right is approval — `approve
iac-builder` has to mean exactly one thing, every time.
