# AIDLC -- the AI Development Life Cycle for OpenCloudOps Playground

The software development life cycle assumes a human writes each change and
another human reviews it. Neither assumption holds when an agent proposes
most of the diff. AIDLC is the loop that replaces them.

## The loop

```
INTENT  ->  CONTEXT  ->  PROPOSE  ->  GATE  ->  ACT  ->  OBSERVE  ->  LEARN
   ^                                                                    |
   +--------------------------------------------------------------------+
```

| Stage | Who | What must be true to leave it |
|---|---|---|
| **Intent** | Human | The goal is written down and links to a ticket. An agent that infers intent from a one-line prompt is guessing, and the guess is invisible. |
| **Context** | Agent | The agent has loaded the platform config, the relevant knowledge, and -- for a data question -- a governed catalog. Context loaded is stated, not assumed. |
| **Propose** | Agent | A diff or a plan exists, with a rationale per step. Nothing is applied yet. |
| **Gate** | Machine + human | Validation passes. For staging, prod, a human approves explicitly. |
| **Act** | Agent | The change is applied to exactly one named environment. |
| **Observe** | Agent | Health is checked against a stated window, not eyeballed. |
| **Learn** | Both | The outcome updates the knowledge base or a guardrail. An incident that changes nothing will recur. |

## The three failures this exists to prevent

**Silent scope drift.** The agent was asked about dev and touched staging.
Prevented at Gate by env-leak detection, and structurally by risk tiers in
`platform.yml`: a HIGH-tier environment cannot be marked autonomous.

**Confident fabrication.** The agent explains a cost rise it never
measured. Prevented by making data access governed and cited -- see
`.github/skills/_research/mesh-research/SKILL.md`. A claim with no evidence
digest is deleted, not hedged.

**Unbounded retry.** The agent loops on a failure, spending tokens and
changing nothing. Prevented by `max_error_chain` and per-node retry
budgets in `agent-graph.yml`.

## How this differs from "just use an agent"

Every stage above has a refusal path. An AIDLC loop where the agent can
never say "I could not establish that" is not a loop, it is a pipeline with
optimism in it. The gates are the product.

## Grading your own loop

1. Can you name the environment every change touched, from the transcript alone?
2. Can you re-derive every factual claim the agent made, from artefacts it left behind?
3. When the agent was blocked, did it stop and say so, or did it improvise?
4. Did anything the agent learned survive the session?

Three yeses and a fourth is a working loop. Two is a demo.
