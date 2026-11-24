# OpenCloudOps

**Agentic-first cloud operations.** An idea goes in; a grounded resource
graph, guarded infrastructure-as-code in five languages, and the SRE setup
to run it come out. Nothing is applied — this framework proposes, a human
executes.

```
  idea / request
        │
        ▼
┌───────────────┐   ground in an   ┌──────────────┐   enforce the   ┌───────────────┐
│  orchestrator  │──approved──────▶│  iac-builder  │──security──────▶│ iac-validator │
│ (junction Qs,  │  pattern         │ (name, tag,   │  baseline        │ (guard.py +   │
│  recall memory)│                  │  render IaC)  │                  │  learner.py)  │
└───────────────┘                  └──────▲────────┘                  └───────┬───────┘
                                            │  bounded repair (≤3 cycles)      │ ok
                                            └───────────────────────────────────┤
                                                                                 ▼
                                              ┌───────────────┐        ┌────────────────┐
                                              │release-executor│◀──────│  sre-observer  │
                                              │ (change record,│        │ (SLOs, alerts, │
                                              │  MEDIUM gate)  │        │  runbook)      │
                                              └───────┬────────┘        └───────┬────────┘
                                                       │                        │
                                                       ▼                        ▼
                                               a person applies      knowledge-curator
                                                                      (memory, next run)
```

Every arrow above is an edge in [`agent-graph.yaml`](agent-graph.yaml), not a
hard-coded pipeline. Every node execution is an OpenTelemetry span; a run's
`request_id` is its trace id.

## Why this exists

Three things an agent should never be trusted to do unsupervised — invent
infrastructure, skip a security control, or apply a change to production —
and three matching structural guarantees:

| Risk | Guarantee | Enforced by |
|---|---|---|
| Inventing a resource nobody reviewed | An agent may only combine nodes from an **approved pattern catalog**. No pattern, no infrastructure — the run fails closed and names the gap. | [`opencloudops/ground.py`](opencloudops/ground.py) |
| Shipping IaC that violates the baseline | Every plan runs through an **enforced security baseline** before anything is emitted. A blocking finding stops the build; three violations have a **deterministic, bounded repair loop** (naming has no more than one correct answer), everything else escalates to a human. | [`opencloudops/guard.py`](opencloudops/guard.py), [`opencloudops/repair.py`](opencloudops/repair.py) |
| An agent applying a production change | The framework **holds no cloud credentials and has no apply endpoint**. `release-executor` produces a change record; a person runs it. Production remediation is excluded from autonomy unconditionally, not by configuration. | [`opencloudops/sre/remediate.py`](opencloudops/sre/remediate.py) |

`tests/test_guard.py::TestBaselineCoverage::test_every_baseline_field_has_a_check`
asserts the first guarantee holds at the code level: a security-baseline
field with no check behind it fails the suite, not just a review.

## What's in the box

- **A resource graph spec** (`*.graph.yaml`) — an application's shape,
  written without provider nouns: `compute.container_service`,
  `store.relational`, `queue.standard`, not "an ECS service" or "an RDS
  instance."
- **A pattern catalog** (`patterns/`) — the only concrete infrastructure the
  framework may emit, each pattern declaring the security `controls` it
  guarantees and the SLO defaults it starts from.
- **A platform contract** (`platform/platform.yaml`) — one file that fixes
  naming, tagging, the security baseline, and the autonomy ladder for an
  organization. Edit it; every agent's output changes with it.
- **Five IaC emitters** — Terraform, CloudFormation, Pulumi (Python), AWS CDK
  (TypeScript), and Kubernetes manifests, all rendered from one resolved
  plan. Kubernetes explicitly declares what it cannot express (a VPC, an RDS
  instance) rather than silently omitting it.
- **A learner** (`opencloudops/learner.py`) — production lessons (no
  `count = length(...)`, pin every module ref, no inline secrets, no
  environment leaking into another environment's artifact) as *executable
  detectors*, not prose nobody re-reads.
- **An agentic SRE plane** (`opencloudops/sre/`) — SLOs and two-window
  burn-rate alerts derived from the plan, a runbook, and an error-pattern
  library that classifies a production signal and proposes a remediation
  with an honest risk tier (and an honest "unrecognised" when nothing
  matches).
- **ChatOps** (`opencloudops/chatops/`) — one deterministic command grammar,
  no model in the approval path. `approve iac-builder` grants a session-only
  MEDIUM gate; `approve release-executor` is refused categorically.
- **A FastAPI service and CLI** — the same agent graph, three surfaces.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for how the pieces fit
together and why the boundaries are where they are.

## Scaffold into any repo, for any coding tool

`occ bootstrap` puts this framework's `occ` CLI in front of five different
coding tools — not just Claude Code:

```bash
occ bootstrap ../some-service-repo --agent claude-code   # .claude/agents/*.md ×6, CLAUDE.md
occ bootstrap ../some-service-repo --agent cursor        # .cursor/rules/*.mdc ×6
occ bootstrap ../some-service-repo --agent copilot       # .github/agents/*.agent.md ×6
occ bootstrap ../some-service-repo --agent continue      # .continue/rules/*.md ×6 + config.json
occ bootstrap ../some-service-repo --agent agnostic      # AGENTS.md only
```

Every tool gets **one file per node in `agent-graph.yaml`** — the graph is
the single source of truth, so a change to a node's zone, tools, or risk
gate changes what gets scaffolded everywhere without a second file to keep
in sync. `occ validate-graph agent-graph.yaml` checks the graph's structure
and a set of safety invariants (no write tool on a node this design calls
read-only, no node ships `HIGH`, every repair loop is bounded, every node
is reachable from the entry point, token budgets are ordered) before
anything is scaffolded from it. See
[`opencloudops/bootstrap/`](opencloudops/bootstrap/).

## Claude Code plugin

This repo is also a Claude Code plugin marketplace. Install it in any
project to get `/opencloudops:plan`, `iac`, `guard`, `slo`, `diagnose`,
`catalog`, `setup`, a `cloud-architect` subagent for end-to-end requests, an
auto-invoked skill that steers Claude toward grounded/guarded IaC instead of
freehand Terraform, and an advisory hook that runs `occ guard` after you
edit a `*.graph.yaml`:

```
/plugin marketplace add find-services-ai/OpenCloudOps
/plugin install opencloudops@opencloudops-plugins
```

The plugin wraps the `occ` CLI (see [Quickstart](#quickstart) below) rather
than vendoring the engine — `/opencloudops:setup` checks for it and walks
through installing it if it's missing. See
[`plugin/opencloudops/`](plugin/opencloudops/) for the plugin's own layout.

## Quickstart

```bash
pip install -e ".[all]"

# See what this installation is allowed to build
occ list patterns

# Ground and guard a reference graph
occ plan reference-web-service --env prod

# Emit infrastructure-as-code
occ iac reference-web-service --env prod --language terraform

# Derive the SRE setup
occ slo reference-web-service --env prod --runbook runbook.md

# Classify a production signal
occ diagnose refsvc --signal "OOMKilled exit code 137" --env prod --remediate

# ChatOps
occ chat plan reference-web-service --env prod
```

Or the full walkthrough, offline, no cloud account or API key required:

```bash
make demo
```

Bring up the service with tracing wired to Jaeger:

```bash
make up            # http://localhost:8081/docs, traces at :16686
make test          # 320+ tests
```

## Test suite: fifteen real applications

`examples/ideas/` holds resource graphs discovered from the fifteen MCP
server applications in the sibling
[`mcp-idea-publisher`](https://github.com/find-services-ai/mcp-idea-publisher)
repository — services written independently of this framework, which is
what makes them a fair test. `opencloudops/integrations/ideas.py` reads a
Dockerfile and a requirements file to infer compute shape and dependencies;
it never guesses at owner or cost centre, and the guard's `GOV-006` rule
blocks a plan whose tags still carry a placeholder. Regenerate with
`make examples`.

`tests/test_ideas_suite.py` runs every one of those fifteen graphs through
grounding, guarding, and all five emitters, in all three environments.

## Integration

- **[find-services-ai/ethos](https://github.com/find-services-ai/ethos)**
  registers this framework as a `platform`-domain agent in the GOS harness
  (`harness/agents/opencloudops/`), with its own §8.6 structural constraint
  ("no agent may apply infrastructure") and three ethos principles
  (`grounded-not-invented`, `propose-never-apply`, `refuse-rather-than-guess`).
- **[find-services-ai/mcp-idea-publisher](https://github.com/find-services-ai/mcp-idea-publisher)**
  calls this framework's API from `tools/opencloudops_tool.py`, so the AI OS
  content/task pipeline can ask "what would it take to run this idea" and
  get back a plan, IaC, and an SRE setup — never an applied change.

## Deployment

- `Dockerfile` — non-root, read-only root filesystem, health-checked on
  `/ready` (which actually loads the platform contract and pattern catalog,
  not just "the process is alive").
- `docker-compose.yml` — the service plus an OpenTelemetry collector and
  Jaeger, so "every request is one trace" is something you can check.
- `deploy/helm/` — safe-by-default: no ingress, no cloud-facing service
  account permissions, a default-deny `NetworkPolicy`, unless you turn them
  on.
- `.github/workflows/ci.yml` — tests on 3.11/3.12, `terraform validate`
  against the generated HCL, a container that must come up non-root and
  report `/ready`, and `helm lint` on two configurations.

## Prior art, vendored and ported

This project started from a prompt-driven Copilot/Claude framework that
turned a coding-agent chat into an IaC/SRE assistant via markdown
instructions and MCP tool contracts, later evolved into
[egen/junction](https://github.com/egen/junction). Two things were
carried forward deliberately, not just referenced:

- **`opencloudops/bootstrap/`** ports that project's `agent_adapters/`
  system faithfully: the same five adapter names, the same per-tool file
  formats, corrected against junction's "one file per graph node" model
  rather than the original's coarser per-tool summary. `graph_invariants.py`
  ports the category of safety checks junction's `graph_schema.py` enforces
  (a JSON Schema plus explicit invariants), adapted to this framework's own
  node names and its "nothing ships HIGH" design.
- **`.github/agents/`, `.github/skills/`, `.github/instructions/`, and
  `.github/knowledge/`** are the original's prompt-driven definitions,
  kept as reference. `adapters/` documents the `platform.yml` shape for
  three concrete infrastructure stacks from that earlier design. Neither
  is depended on by `opencloudops/`, which supersedes their runtime role
  with an executing engine rather than a set of instructions for a human
  to interpret.

## License

[MIT](LICENSE)
