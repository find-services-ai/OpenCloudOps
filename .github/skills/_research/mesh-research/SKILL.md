---
name: mesh-research
description: >
  Governed cross-plane research through the sandbox-research MCP server.
  Use for any question that spans data domains. Do not use for a
  single-source query a person can run directly.
---

# mesh-research

Planes available on this platform: bi, lake, warehouse.
Server: `sandbox-research` (find-services-ai/mcp-idea-publisher,
`ideas/sandbox-research`). 17 tools.

## The loop

```
sandbox_create -> catalog_search/describe -> research_plan
               -> research_run -> research_report -> sandbox_destroy
```

## Four rules that are not style preferences

**Narrowest scope that answers the question.** Not the widest that might be
useful later. A sandbox is cheap to create and its scope is the only thing
standing between a research question and every row in the mesh.

**A refusal is information.** `reason: "classification"` means pass
`select` with permitted columns. It does not mean create a sandbox with PII
clearance -- only a person decides a question is worth reading PII for.

**Every claim cites evidence.** `research_report` returns findings with
evidence ids and digests. Do not add prose that outruns them. An uncited
claim is deleted, not softened.

**Gaps are reported.** `research_plan` names the planes it could not cover.
Carry that into the summary. A report with no limits section is not a
cleaner report, it is a less honest one.

## Honest description of the boundary

The sandbox is a policy, scope and spend envelope with an audit ledger: a
TTL, a query and row budget, a plane/domain scope, a classification
clearance, and a default-deny network policy, all checked before a backend
is touched. It is **not** kernel isolation -- no VM, no container, no
seccomp. If someone asks whether untrusted code is safe inside it, the
answer is no, not on this alone.

## Cost

- `catalog_describe` before `query_product`, always.
- Explicit `select`, always. Reading every column to look at two is how a
  PII column reaches a transcript.
- A `where` before a bigger `max_rows`.
- If the question needs tens of millions of rows, say so. It belongs in the
  warehouse, not in a research sandbox.
