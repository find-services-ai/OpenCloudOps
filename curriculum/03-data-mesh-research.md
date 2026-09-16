# 03 — Cross-plane research without centralising

Labs: `lab01_first_sandbox.py`, `lab02_cross_plane_join.py`

## The mesh tax

A data mesh gives each domain ownership of its own products on whatever
plane suits it. That is the right trade for the teams who run them, and it
costs you the ability to answer a question that crosses domains.

"Why is cloud spend rising, and which incidents and deploys explain it?"
needs the warehouse (cost), the lake (incidents, deploys), and the BI tool
(what the business was told). Four credentials, four query languages, an
afternoon — and an answer that arrives as a screenshot nobody can re-derive.

The two usual responses are both worse than the afternoon:

- **Centralise.** Copy everything into one warehouse. This undoes the mesh,
  doubles storage, and puts every domain team behind a central pipeline team.
- **Hand an agent broad credentials.** Fast answers of unknown provenance,
  unbounded spend, and a PII column in a transcript.

## The third option

Leave the data where it is. Unify the **description** of it. Put every read
behind a policy.

```
<plane>.<domain>.<name>

warehouse.finops.cost_by_service
lake.cloudops.incidents
bi.reporting.exec-cloud-spend
```

One address space, one search, one lineage graph. No bytes move. Each
domain still runs its own store, its own retention, its own access — the
catalog indexes descriptions, not contents.

## Why planning is deterministic here

`research_plan` is catalog retrieval, not model inference. Three reasons,
and they are worth disagreeing with if you can:

1. **Reproducible.** The same question against the same catalog yields the
   same plan. A finding can be re-derived next month, and a disagreement is
   about the data rather than about sampling.
2. **Inspectable.** Each step carries `catalog match score 20 on
   description:cloud, name:cost`. "Why did it read that table?" has a
   literal answer.
3. **Honest about gaps.** Retrieval can report what it did *not* find. A
   generative planner tends to produce a plausible step against a product
   that does not exist.

The model is genuinely better one layer up: turning a vague ask into good
search terms, and turning cited findings into prose. That is the agent
calling these tools — which is why the server never calls an LLM itself.

## Union and join

`unified_query` reads several products and returns one result set. Each row
carries `_source` (union) or `_sources` (join), so provenance survives the
merge.

The join is an in-memory hash join under the row budget, and that ceiling
is deliberate. If a question needs tens of millions of rows, it belongs in
the warehouse. A federated layer that quietly becomes a query engine is a
query engine nobody chose, tuned, or capacity-planned.

Critically: **each source is governed separately.** A lake source inside a
warehouse-only sandbox is refused even when the warehouse source is fine.
Unification is not a side door around scope. `lab02` proves this.

## Exercise

After `lab02`, answer: what would centralising this data have bought that
the catalog does not? There is a real answer — sub-second joins over full
history, and one query dialect. Decide whether your questions need it. Most
research questions do not; most dashboards do.
