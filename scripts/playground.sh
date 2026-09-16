#!/usr/bin/env bash
# Full playground run: scaffold check, mesh build, every lab, evaluation.
#
#   ./scripts/playground.sh
#   SANDBOX_RESEARCH_PATH=/path/to/ideas/sandbox-research ./scripts/playground.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [ -z "${SANDBOX_RESEARCH_PATH:-}" ]; then
  CANDIDATE="$(cd .. && pwd)/mcp-idea-publisher/ideas/sandbox-research"
  if [ -f "$CANDIDATE/src/tools.py" ]; then
    export SANDBOX_RESEARCH_PATH="$CANDIDATE"
  else
    echo "sandbox-research not found. Clone find-services-ai/mcp-idea-publisher" >&2
    echo "next to this repo, or set SANDBOX_RESEARCH_PATH." >&2
    exit 1
  fi
fi
echo "sandbox-research: $SANDBOX_RESEARCH_PATH"

echo; echo "== bootstrap (dry run) =="
python3 -m bootstrap --dry-run

echo; echo "== labs =="
for lab in labs/lab*.py; do
  echo; echo "-- $lab"
  python3 "$lab"
done

echo; echo "== evaluation =="
python3 evaluation/evaluate.py --demo

echo; echo "== tests =="
python3 -m pytest

echo; echo "Playground complete."
