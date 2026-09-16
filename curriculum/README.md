# CloudOps Agentic AI + AIDLC — curriculum

Six modules. Each one pairs a short written module with a lab you run
against a real governed tool surface, not a mock.

The premise: people learn agentic engineering from **watching an agent be
refused**, not from watching it succeed. A demo where everything works
teaches nothing about the day it does not. Every lab here deliberately
hits a wall.

| # | Module | Lab | You can afterwards |
|---|---|---|---|
| 00 | [AIDLC](00-aidlc.md) | — | Name the seven stages and the refusal path in each |
| 01 | [Agent graphs and zone defense](01-agent-graphs.md) | `bootstrap --dry-run` | Read a graph and say what it forbids |
| 02 | [Governed tools](02-governed-tools.md) | `lab04` | Design a tool whose refusals are useful |
| 03 | [Data mesh research](03-data-mesh-research.md) | `lab01`, `lab02` | Answer a cross-plane question with citations |
| 04 | [Lineage and blast radius](04-lineage-and-blast-radius.md) | `lab03` | Say who breaks, and what your answer cannot see |
| 05 | [Evaluating a run](05-evaluating-a-run.md) | `lab05` | Grade a research run and defend the grade |

## Setup

```bash
git clone https://github.com/find-services-ai/mcp-idea-publisher ../mcp-idea-publisher
export SANDBOX_RESEARCH_PATH=../mcp-idea-publisher/ideas/sandbox-research

python3 labs/lab01_first_sandbox.py
```

Everything runs locally against a synthetic mesh generated from a fixed
seed. No cloud account, no credentials, no real data.

## How to teach it

Run each lab twice. The first time, read the output. The second time,
**break it on purpose** — narrow the sandbox further, starve the budget,
point a connector at a file that does not exist. The failure messages are
the curriculum; the happy path is just the setup for them.

## Assessment

A learner is done when they can take an unfamiliar question, scope a
sandbox for it, produce a cited report, and then argue against their own
report using `evaluation/rubric.yaml` — naming at least one thing it
cannot support.
