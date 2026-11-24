# Contributing to OpenCloudOps

Thanks for your interest. This is a small, opinionated framework — the
guide below is short on purpose.

## Ways to contribute

### Report a bug

Open a [bug report](.github/ISSUE_TEMPLATE/bug_report.md). Include the
command or API call, the graph/platform contract involved (redact real
account IDs or endpoints), and observed vs. expected behavior.

### Add or fix a pattern

Patterns (`patterns/*.yaml`) are the only infrastructure this framework may
emit, so a pattern change is a reviewed act:

1. Every `controls` entry must correspond to something the emitters
   actually render for that pattern — `tests/test_emitters.py` checks the
   generated output against its own lessons, but a control you *claim*
   without rendering is a security defect, not a typo, and nothing catches
   an unrendered claim automatically. Check by hand.
2. Add or update the emitter logic in all five backends
   (`opencloudops/emitters/`) for the new node type or parameter — a
   pattern with no emitter support fails at `emit()`, not at pattern
   registration.
3. Run `tests/test_guard.py` — if the pattern changes what the security
   baseline should check, add a `guard.py` rule and confirm
   `covered_baseline_fields()` still matches every `SecurityBaseline` field.

### Add a production lesson

Lessons (`opencloudops/learner.py`) must ship with a detector — a lesson
registered without one fails to import. Write the regex or check first,
prove it fires against something it should catch and *not* fire against a
correct variant (see `tests/test_learner_sre.py::TestDetectorsFire` for the
pattern), then register it with `@lesson(...)`.

### Add an IaC backend

Subclass `opencloudops.emitters.base.Emitter`, set `id`/`language`, declare
`unsupported` node types honestly (Kubernetes has no VPC projection and
says so via `UNSUPPORTED.md` rather than silently omitting it), and add it
to `opencloudops/emitters/__init__.py`. It picks up
`tests/test_emitters.py`'s parametrized checks (deterministic,
carries resolved names, passes its own lessons, no cross-environment leak)
automatically.

### Change the agent graph

`agent-graph.yaml` is data, not code — most behavior changes (a new node, a
different repair-cycle limit, a stricter token budget) are edits there.
Zone overlaps and unparseable risk gates fail to load; run
`tests/test_agentgraph.py` after any change.

## Non-negotiables

These aren't style preferences — they're the properties the rest of the
design depends on:

- **No apply path.** This framework proposes; it never holds cloud
  credentials or exposes a way to execute a change. `release-executor` is
  `MEDIUM`-gated because it writes a *document*; a HIGH gate is reserved for
  a capability that does not exist here, and the API/ChatOps refuse HIGH by
  tier so it can't be switched on quietly if one is ever added.
- **Fail closed.** No approved pattern → refuse and name the gap, don't
  improvise. Unrecognised production signal → report "unrecognised" at LOW
  confidence, don't guess.
- **Grounding is structural, not a convention.** An emitter reads only a
  `ResolvedPlan`; it cannot see a raw graph node, so it cannot render
  something the guard never checked.
- **Production is excluded from autonomy unconditionally** in the
  remediation path (`sre/remediate.py`), independent of the platform's
  autonomy ladder — see the code comment there before changing it.

## Testing

```bash
pip install -e ".[all]"
make test      # PYTHONPATH=. python3 -m pytest tests/ -q
make lint
make demo      # end-to-end, offline
```

## Code of Conduct

See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
