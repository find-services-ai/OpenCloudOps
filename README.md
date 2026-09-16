# OpenCloudOps

**A training playground for CloudOps agentic AI and AIDLC.**

Six curriculum modules, five runnable labs, a repo bootstrap, and an
evaluation harness — all driving a real governed tool surface rather than a
mock. No cloud account, no credentials, no real data.

The system under study is the **Sandbox AI Research System**
(`find-services-ai/mcp-idea-publisher`, `ideas/sandbox-research`): on-demand
governed research sandboxes over a unified data-mesh catalog. CloudOps is
the sample domain — cloud cost, incidents, deploys, and the dashboards
built on them — because it is a domain where every learner already knows
what a wrong answer costs.

---

## Quickstart

```bash
git clone https://github.com/find-services-ai/mcp-idea-publisher ../mcp-idea-publisher
export SANDBOX_RESEARCH_PATH=../mcp-idea-publisher/ideas/sandbox-research

pip install -r requirements.txt
./scripts/playground.sh        # bootstrap + all five labs + evaluation + tests
```

Or one lab at a time:

```bash
python3 labs/lab01_first_sandbox.py
python3 evaluation/evaluate.py --demo
```

---

## What is here

| Path | What |
|---|---|
| `curriculum/` | Six modules: AIDLC, agent graphs, governed tools, mesh research, lineage, evaluation |
| `labs/` | Five runnable labs against the real tool surface |
| `bootstrap/` | Scaffold a CloudOps agentic-AI repo: agent graph, skills, guardrails, mesh manifest |
| `evaluation/` | Rubric + scorer for a research run; usable as a CI gate |
| `.github/`, `CLAUDE.md`, `agent-graph.yml`, `mesh.yaml` | This repo, scaffolded by its own bootstrap |
| `tests/` | 37 tests, including a smoke run of every lab |

This repo is scaffolded by the bootstrap it ships. If the bootstrap breaks,
the repo demonstrating it breaks too — which is the point.

---

## The teaching premise

People learn agentic engineering from watching an agent **be refused**, not
from watching it succeed. A demo where everything works teaches nothing
about the day it does not.

So every lab hits a wall deliberately:

- `lab02` asks for a table with a PII column and reads the refusal.
- `lab03` finds a product with no declared lineage and sits with what that
  means for a blast-radius answer.
- `lab04` hits all six governance boundaries on purpose.
- `lab05` runs a *sloppy* research run and watches the rubric reject it.

Run each lab twice — once as written, once after breaking something.

---

## The bootstrap

```bash
python3 -m bootstrap --dry-run                     # preview
python3 -m bootstrap --target ../my-cloudops-repo  # scaffold
python3 -m bootstrap --profile minimal --agent copilot --target .
python3 -m bootstrap --config ./platform.yml --target . --overwrite
```

Scaffolds ten files: `platform.yml`, `agent-graph.yml`, AIDLC and guardrail
instructions, three skills, a mesh manifest, and an agent entrypoint
(`CLAUDE.md` for Claude Code, `AGENTS.md` always).

Two properties worth the attention:

**Guardrails are derived, never typed twice.** A node's risk gate comes from
the highest-risk environment in `platform.yml`. The promotion path is
derived from risk tiers. You cannot scaffold a graph whose guardrails
disagree with its environments, because the tiers are stated in one place.
`Environment.__post_init__` refuses a HIGH-tier environment marked
autonomous — a guardrail a config key can switch off is decoration.

**A research node exists only when a mesh is declared.** No mesh, no
`mesh-researcher` — and the generated graph says why, rather than leaving a
silent hole. An agent asked "why did cost go up" with no governed way to
look is an agent that guesses.

Shape adapted from the IaC/SRE agent-graph bootstrap referenced in the
brief; the mesh and research layers are what is new.

---

## The evaluation harness

```bash
python3 evaluation/evaluate.py --demo
```

Grades a research run on eight criteria — completeness of citations,
restricted-data containment, reproducibility of digests, declared gaps,
plane coverage, scope minimality, budget discipline, sandbox closure.

It grades the **run**, not the conclusion. Whether the answer is correct is
a domain question no rubric settles. Whether the run was scoped, cited,
reproducible and honest about its gaps is decidable from artefacts — and a
run failing those is untrustworthy even when its conclusion is right.

Two criteria are disqualifying: an uncited finding and a leaked restricted
column cap the verdict at `untrustworthy` whatever the score. Neither is
recoverable by re-running.

[curriculum/05](curriculum/05-evaluating-a-run.md) documents three
loopholes this rubric actually had, and how each closed. Your first rubric
will have the same shape.

---

## Honest boundaries

**The research sandbox is not kernel isolation.** It is a policy, scope and
spend envelope with an audit ledger: a TTL, a query and row budget, a
plane/domain scope, a classification clearance, and a default-deny network
policy, all checked before a backend is touched. No VM, no container
boundary, no seccomp. Run untrusted analysis code inside real isolation as
well. `tests/test_bootstrap.py` asserts that no document scaffolded here
claims otherwise.

**The mesh is synthetic.** `fixtures/seed_mesh.py` generates cost,
incident, deploy and dashboard data from a fixed seed with
`example.invalid` addresses. Nothing here is a real account, a real
incident, or a real person.

**Lineage is declared, not discovered.** A blast-radius answer is exactly as
complete as the declarations behind it, and no tool can tell you which
declarations are missing. `lab03` makes you sit with that.

**`OpenCloudOps.zip`** in this repo is a truncated upload from an earlier
commit — `unzip` reports a corrupt central directory. It is left in place
rather than deleted, since removing someone's archive is their call. Nothing
here depends on it.

---

## Related

- **`find-services-ai/mcp-idea-publisher`** — `ideas/sandbox-research`, the
  MCP server this playground drives. 17 tools, 93 tests.
- **`find-services-ai/ethos`** — the GOS harness. The agent is registered as
  `sandbox-research` (domain `professional`, ethos-gated). `cd harness && gos list`.

## Licence

MIT. See [LICENSE](LICENSE).
