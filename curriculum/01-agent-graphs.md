# 01 — Agent graphs and zone defense

## One agent with twelve tools is not a system

The default shape of an agentic build is one agent holding every tool.
It works in a demo and degrades predictably: the agent that can both write
Terraform and validate it will validate its own work generously, because
the same context that produced the change also produced the confidence in
it.

**Zone defense** means every node owns a non-overlapping set of concerns
and a non-overlapping set of tools. The value is not modularity. It is that
a node with no `edit` tool *cannot* quietly fix what it was asked to check.

Look at the research graph in
`ideas/sandbox-research/agent-graph.yml`: `catalog-navigator` may call
`catalog_search` and `catalog_describe`, and it may **not** call
`query_product`. Separating "which data" from "the data" is what makes
retrieval auditable — the node choosing what to read cannot also read it
and then rationalise the choice.

## Edges are policy, not plumbing

```yaml
- { from: mesh-querier, to: evidence-auditor, trigger: always, gate: must-pass }
- { from: evidence-auditor, to: report-writer, trigger: "all claims cited" }
```

`report-writer` is reachable **only** through `evidence-auditor`. That is a
structural guarantee that no prose is written from unverified data. A
prompt saying "always cite your sources" is a request. A graph with no edge
from querier to writer is a constraint.

## Gates come in three strengths

| Gate | Meaning | Example |
|---|---|---|
| `none` | Proceed | orchestrator routing |
| `must-pass` | Blocked until a check passes | `change-validator` |
| Risk-tier (`LOW`/`MEDIUM`/`HIGH`) | Autonomy depends on the environment | `iac-builder` |
| `sandbox-policy` | Every call checked against a live policy | `mesh-researcher` |

In `bootstrap/graph.py` the risk gate is **derived** from the highest-risk
environment in `platform.yml`, never typed by hand. You cannot scaffold a
graph whose guardrails disagree with its environment tiers, because the
tiers are stated in exactly one place. `Environment.__post_init__` refuses
a HIGH-tier environment marked autonomous outright — a guardrail a config
key can switch off is decoration.

## Error decontamination

```yaml
on_node_failure: snapshot the failure, isolate it, do not carry it forward
on_retry: reload fresh; never reuse a stale plan
max_error_chain: 3
```

The failure mode this prevents: an agent fails, keeps the failed context,
retries with context now containing its own confusion, and converges on
nonsense with increasing confidence. Fresh context per retry, and a hard
stop at three.

## Exercise

```bash
python3 -m bootstrap --dry-run                 # full platform
python3 -m bootstrap --profile minimal --dry-run
```

Diff the two `agent-graph.yml` outputs. The minimal profile declares no
mesh, so no `mesh-researcher` node exists — and the graph says why, rather
than leaving a silent hole. Then ask: which node in the full graph could
you delete and still be safe? (There is one plausible answer and one
that will hurt.)
