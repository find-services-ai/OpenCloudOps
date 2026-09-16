# Evaluation

Grades one research run against `rubric.yaml`.

```bash
python3 evaluation/evaluate.py --demo            # run a question, then grade it
python3 evaluation/evaluate.py --plan-id plan_... --db ./research.db
python3 evaluation/evaluate.py --demo --json     # machine-readable
```

Exit code is 0 for `trustworthy` / `usable-with-caveats`, 1 otherwise — so
it works as a CI gate on a scheduled research job.

It grades the **run**, not the conclusion: scope, citations,
reproducibility, and honesty about gaps. Whether the conclusion is correct
is a domain question a rubric cannot settle; whether the run can be
defended is decidable from what it left behind.

Two criteria are disqualifying — an uncited finding and a restricted column
in the evidence cap the verdict at `untrustworthy` whatever the score,
because neither is recoverable by re-running.

Adding a criterion: add it to `rubric.yaml` and implement `_<id>` on
`Evaluator`. `tests/test_evaluation.py` fails until both exist. Then try to
construct the laziest run that passes it — if that takes under a minute,
the criterion is decoration. See
[curriculum/05](../curriculum/05-evaluating-a-run.md) for three real
examples of that going wrong here.
