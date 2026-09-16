# 04 — Lineage: who breaks, and what you cannot see

Lab: `python3 labs/lab03_blast_radius.py`

## Blast radius before root cause

During an incident, two different people are asking two different
questions. The engineer asks what broke. Everyone else asks whether the
number in front of them is wrong.

The second question is answerable faster, and answering it first buys the
time to do the first properly. `catalog_lineage(direction="down")` returns
the products and dashboards downstream of a change, with owners — a
notification list before you have a diagnosis.

## Lineage crosses planes because someone declared it

A BI tool is the only system that knows its dashboard reads
`warehouse.finops.cost_by_service`. The warehouse does not know it is being
read. That declaration in the BI definition is what makes cross-plane
lineage answerable with no separate lineage service:

```yaml
- name: exec-cloud-spend
  upstream: [warehouse.finops.cost_by_service]
```

## The limit, stated plainly

The catalog reports **declared** lineage. In `lab03`, `deploy_events` shows
no upstream — not because it has none, but because nothing declared one.

A blast-radius answer is exactly as complete as the declarations behind it,
and no tool can tell you which declarations are missing. This is the honest
failure mode of every lineage product, including the expensive ones. The
ones that scan SQL to infer lineage trade one incompleteness for another:
they miss what happens outside SQL.

`catalog_lineage` returns edges rather than a confidence score for this
reason. A number would imply the tool knows how much it is missing. It does
not, and neither do you.

## What to do about it

- Treat a product with no declared upstream as **unknown**, not as a root.
- When an incident reveals an undeclared dependency, declare it. That is
  the Learn stage of AIDLC doing its job.
- In a report, say "one declared consumer" rather than "one consumer". The
  extra word is the difference between a claim you can defend and one you
  cannot.

## Cycles

Real mesh definitions contain cycles — A reads B reads A, usually through a
materialisation nobody meant to make circular. The lineage walk seeds
`visited` with the root so a cycle terminates rather than spinning until
the depth budget runs out. `test_lineage_terminates_on_a_cycle` builds one
deliberately.

## Exercise

Your change is approved and lineage shows one dashboard. What would you
check before believing that is the whole blast radius? Write three specific
checks. Then ask which of them could be automated into a declaration.
