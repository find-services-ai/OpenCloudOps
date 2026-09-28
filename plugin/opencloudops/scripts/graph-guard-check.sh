#!/usr/bin/env bash
# PostToolUse advisory: after a *.graph.yaml is written or edited, run
# `occ guard` against it and hand Claude the verdict as additionalContext.
#
# Best-effort and strictly non-blocking by design -- this always exits 0. A
# hook that fails hard on a missing `occ`, an unresolvable graph, or a repo
# that doesn't use OpenCloudOps at all would make installing this plugin
# risky; the worst this script may do is say nothing.
set -u

input="$(cat)"
file_path="$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty' 2>/dev/null)"
[ -z "$file_path" ] && exit 0

# Only ever look at *.graph.yaml -- the hooks.json `if` matcher already
# restricts this, but a second check costs nothing and this script may be
# reused directly.
case "$file_path" in
  *.graph.yaml) ;;
  *) exit 0 ;;
esac

command -v occ >/dev/null 2>&1 || exit 0

project_dir="${CLAUDE_PROJECT_DIR:-$PWD}"
[ -f "$project_dir/platform/platform.yaml" ] || exit 0
[ -d "$project_dir/patterns" ] || exit 0

graph_name="$(basename "$file_path" .graph.yaml)"
graph_dir="$(dirname "$file_path")"

output="$(cd "$project_dir" && OCC_GRAPHS="$graph_dir" occ guard "$graph_name" --env dev 2>&1)"
status=$?

summary="$(printf '%s' "$output" | tail -20)"
if [ $status -ne 0 ]; then
  context="OpenCloudOps guard found blocking findings in ${file_path} (dev environment):
${summary}"
else
  context="OpenCloudOps guard passed for ${file_path} (dev environment):
${summary}"
fi

jq -n --arg ctx "$context" '{
  hookSpecificOutput: {
    hookEventName: "PostToolUse",
    additionalContext: $ctx
  }
}'
exit 0
