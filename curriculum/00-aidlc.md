# 00 — AIDLC: the loop that replaces code review

## Why the SDLC does not survive contact with agents

The software development life cycle rests on two assumptions: a human
wrote each change, and a different human read it before it shipped. When an
agent produces most of the diff, both fail at once — there is more change
than review capacity, and the reviewer is now reviewing output rather than
reasoning.

The common response is to slow the agent down until human review keeps up.
That gives up the speed without buying back the safety, because a reviewer
skimming forty agent-authored diffs is not reviewing.

AIDLC replaces *human reads everything* with *every stage has a gate and a
refusal path*.

## The loop

```
INTENT  ->  CONTEXT  ->  PROPOSE  ->  GATE  ->  ACT  ->  OBSERVE  ->  LEARN
   ^                                                                    |
   +--------------------------------------------------------------------+
```

**Intent** — a human states the goal in writing, linked to a ticket. An
agent that infers intent from one line is guessing, and the guess is
invisible until it is expensive.

**Context** — the agent loads the platform config, the relevant knowledge,
and a governed way to look at data. It states what it loaded. "I read the
repo" is not context; naming the files is.

**Propose** — a diff or a plan with a rationale per step. Nothing applied.
The rationale is what makes review possible at agent speed: you review the
reasoning, not forty diffs.

**Gate** — machine validation, plus a human for high-risk environments.
This is the stage people delete first and regret first.

**Act** — applied to exactly one named environment.

**Observe** — health checked against a stated window. Not "looks fine".

**Learn** — the outcome updates a guardrail or the knowledge base. An
incident that changes nothing recurs, and you have paid for it twice.

## The refusal path

Each stage needs an answer to "what if the agent cannot do this?"

| Stage | Refusal |
|---|---|
| Intent | "This request is ambiguous between X and Y." Stop. |
| Context | "The data needed is not in the catalog." Name what is missing. |
| Propose | "I can do the first half; the second needs a decision you own." |
| Gate | "Validation failed for this reason." Never route around it. |
| Act | "This would touch two environments." Refuse and split. |
| Observe | "Health is ambiguous after the window." Escalate, do not declare. |
| Learn | "This failure has no generalisable lesson." Say so; do not invent one. |

An AIDLC loop where the agent can never say "I could not establish that" is
not a loop. It is a pipeline with optimism in it.

## What this looks like in code

The `sandbox-research` server makes the Context and Gate stages
mechanical rather than cultural:

- Context is `catalog_search` / `catalog_describe` — the agent cannot read
  what it has not located, and the location is recorded.
- Gate is the sandbox policy — TTL, budget, scope, classification — checked
  before a backend is touched.
- Refusal is the return value: `{"error", "reason", "hint"}`.

You can enforce a culture of citation, or you can make an uncited claim
structurally impossible. The second one survives a bad week.

## Exercise

Take the last incident you worked. Write out the seven stages as they
actually happened. Then mark which gates existed and which were assumed.
Most teams find Observe and Learn were assumed.
