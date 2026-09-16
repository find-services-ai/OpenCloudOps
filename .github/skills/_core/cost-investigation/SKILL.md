---
name: cost-investigation
description: >
  Investigate a cloud cost change across the data mesh -- warehouse cost
  tables, deploy events, incidents, and the BI snapshot the business
  actually saw. Use when asked why spend moved, not to produce a routine
  cost report.
---

# cost-investigation

## Why this is a skill and not a query

"Why did spend go up" is never answered by the cost table alone. The cost
table says *what*. The lake says what was deployed and what broke. The BI
snapshot says what the business was told. A cost rise that coincides with a
rollback storm is a different problem from one that coincides with a new
region, and the cost table cannot tell them apart.

## Procedure

1. **Scope the sandbox.** Narrowest plane set that could answer it.

   ```
   sandbox_create(
     purpose="cost investigation: <the actual question>",
     ttl_seconds=1800, max_queries=40, max_rows=25000,
     allowed_planes=["warehouse", "lake", "bi"],
   )
   ```

2. **Orient before reading.** `catalog_search("cost weekly service")` then
   `catalog_describe` on the top hits. Reading a table to find out what is
   in it is the most expensive way to ask a cheap question.

3. **Establish the shape.** Which service, which environment, which weeks.
   One `query_product` with an explicit `select` and a `where`.

4. **Correlate across planes.** `unified_query` joining incidents or deploy
   events to service ownership on `service`. This is the step the cost
   table cannot do alone.

5. **Check what the business saw.** Read the BI dashboard rows. If your
   number disagrees with the published one, that disagreement *is* a
   finding -- report it, do not quietly prefer your own.

6. **Report with citations.** `research_report`. Every claim carries an
   evidence digest.

## Refusals

- `classification` -> narrow `select`. Owner emails are PII; team and tier
  usually answer the question without them.
- `budget` -> add a `where` before raising `max_rows`. A filter is cheaper
  than a bigger budget and produces a better answer.
- A plane is unreachable -> say which conclusions are therefore unsupported.

## Done means

A named mechanism, a cited number, and an explicit list of what you could
not check. "Costs rose across the board" with no mechanism is not done.
