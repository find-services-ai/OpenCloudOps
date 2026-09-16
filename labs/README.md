# Labs

Five runnable labs against the real `sandbox-research` tool surface. No
mocks — `_harness.call()` dispatches to the same `handle_tool` function the
MCP server uses.

```bash
export SANDBOX_RESEARCH_PATH=../mcp-idea-publisher/ideas/sandbox-research
python3 labs/lab01_first_sandbox.py
```

| Lab | Teaches | Module |
|---|---|---|
| `lab01_first_sandbox.py` | The full loop; a plan that states its own reasoning and its own gaps | [03](../curriculum/03-data-mesh-research.md) |
| `lab02_cross_plane_join.py` | Joining across planes; unification does not widen scope | [03](../curriculum/03-data-mesh-research.md) |
| `lab03_blast_radius.py` | Lineage, owners, and the limit of declared lineage | [04](../curriculum/04-lineage-and-blast-radius.md) |
| `lab04_governance_boundaries.py` | All six refusals, on purpose | [02](../curriculum/02-governed-tools.md) |
| `lab05_evaluate_a_run.py` | Grading a disciplined run against a sloppy one | [05](../curriculum/05-evaluating-a-run.md) |

Each lab prints `[PASS]` / `[FAIL]` per check and exits non-zero if any
check failed. `lab05` deliberately *prints* failing rubric rows for its
sloppy run — that is the lesson, not a broken lab.

Run them twice: once as written, once after breaking something on purpose.
The failure messages are the curriculum.

Every lab is smoke-tested in `tests/test_labs.py`, so a lab that rots
fails CI rather than a student.
