---
name: incident-triage
description: >
  Triage a cloudops incident: establish blast radius, correlate
  with deploys, and produce a cited timeline. Use for an active or recent
  incident, not for routine health checks.
---

# incident-triage

## Order of operations

1. **Blast radius first, cause second.** `catalog_lineage(product_id=...,
   direction="down")` tells you which dashboards and datasets are affected
   before you know why. People asking "is my report wrong" need an answer
   sooner than people asking "what broke".

2. **Correlate deploys.** Read `deploy_events` filtered to the service and
   window. A `rolled_back` or `failed` outcome immediately before the
   incident is a lead, not a conclusion.

3. **Read the incident record.** Root cause, detection time, restore time.

4. **Name the mechanism.** "Flake", "transient", and "intermittent" are not
   root causes. If you cannot name the mechanism, say that you could not,
   and say what you would need to.

## What not to do

- Do not propose a fix to a source system. This agent reads and describes.
  Name what would have to do the write, and who owns it.
- Do not extrapolate from one incident to a trend. Two data points and a
  slope is not a trend, it is a line.
- Do not omit the plane you could not read. An incident report missing the
  BI plane is a report with a hole in it, and the hole belongs in the
  limits section.

## Output

A timeline with a cited row behind each entry, a named blast radius from
lineage, and a limits section. If the limits section is empty, you have not
finished looking.
