#!/usr/bin/env python3
"""Regenerate examples/ideas/ from the sibling mcp-idea-publisher applications.

These fifteen MCP servers were written independently of this framework, which
is exactly what makes them a fair test suite: nothing about them was shaped to
make OpenCloudOps look good.

Discovery returns a draft with placeholder owner and cost-centre values, which
the guard's tagging check refuses. This script fills those in with the values a
platform team would supply, so the committed examples are plans that actually
pass -- and `tests/test_ideas_suite.py` asserts both halves: the draft is
refused, the completed graph is clean.

Usage:
    python scripts/generate_ideas_examples.py [path/to/ideas] [--check]

`--check` regenerates into memory and fails if the committed files differ, so
CI catches an example that drifted from the discovery logic.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from opencloudops.integrations.ideas import discover_all, to_yaml  # noqa: E402
from opencloudops.spec import DataClassification  # noqa: E402

DEFAULT_IDEAS = Path("../mcp-idea-publisher/ideas")
OUT = REPO / "examples" / "ideas"

#: Values discovery cannot infer from a repository. Supplying them here is the
#: same act a platform team performs once per service.
OWNER = "platform-team"
COST_CENTER = "CC-1042"

#: Applications that handle legal, financial or immigration material get the
#: stricter classification. This is a judgement, so it is written down rather
#: than inferred.
RESTRICTED = {"sba-legal-assistant", "visa-assist", "visa-browser-agent", "financial-advisor"}


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    check = "--check" in sys.argv
    ideas_dir = Path(args[0]) if args else (REPO / DEFAULT_IDEAS)
    if not ideas_dir.is_dir():
        print(f"no ideas directory at {ideas_dir}", file=sys.stderr)
        return 2

    OUT.mkdir(parents=True, exist_ok=True)
    drift: list[str] = []
    written = 0

    for name, (graph, discovery) in discover_all(ideas_dir).items():
        graph.metadata.owner = OWNER
        graph.metadata.cost_center = COST_CENTER
        graph.metadata.data_classification = (
            DataClassification.restricted if name in RESTRICTED else DataClassification.confidential
        )
        graph.metadata.environments = ["dev", "staging", "prod"]
        rendered = to_yaml(graph, discovery)
        path = OUT / f"{graph.metadata.name}.graph.yaml"
        if check:
            if not path.exists() or path.read_text(encoding="utf-8") != rendered:
                drift.append(path.name)
        else:
            path.write_text(rendered, encoding="utf-8")
            written += 1

    if check:
        if drift:
            print("examples out of date: " + ", ".join(drift), file=sys.stderr)
            print("run: python scripts/generate_ideas_examples.py", file=sys.stderr)
            return 1
        print("examples are up to date")
        return 0
    print(f"wrote {written} graph(s) to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
