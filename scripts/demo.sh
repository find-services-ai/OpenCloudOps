#!/usr/bin/env bash
# OpenCloudOps end-to-end walkthrough.
#
# Runs entirely offline: no cloud account, no API key, no model. Every number
# and every finding below is produced by the code, not printed from a script.
#
#   bash scripts/demo.sh
#   OCC_OTEL_CONSOLE=1 bash scripts/demo.sh   # also print the spans

set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="${PYTHONPATH:-.}"
OCC="python3 -m opencloudops.cli"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

rule() { printf '\n\033[1m── %s\033[0m\n\n' "$1"; }

rule "1. What this installation is allowed to build"
$OCC list patterns

rule "2. The agent graph that will run"
$OCC list nodes

rule "3. Ground and guard a graph for production"
$OCC plan reference-web-service --env prod

rule "4. The same graph, deliberately broken, repaired in flight"
python3 - "$WORK" <<'PY'
import sys, yaml, pathlib
doc = yaml.safe_load(pathlib.Path("graphs/reference-web-service.graph.yaml").read_text())
doc["metadata"]["name"] = "demo-broken"
for node in doc["spec"]["nodes"]:
    if node["id"] == "api":
        node["params"]["desired_count"] = 1        # single task in production
    if node["id"] == "db":
        node["params"]["multi_az"] = False         # single-AZ database
    if node["id"] == "network":
        node["params"]["single_nat_gateway"] = True
    if node["id"] == "db-credentials":
        node["params"]["rotation_days"] = 365      # beyond the baseline maximum
pathlib.Path(sys.argv[1], "demo-broken.graph.yaml").write_text(yaml.safe_dump(doc))
PY
cp "$WORK/demo-broken.graph.yaml" graphs/demo-broken.graph.yaml
trap 'rm -rf "$WORK"; rm -f graphs/demo-broken.graph.yaml' EXIT
echo "Four production violations introduced. The validator hands the plan back"
echo "to the builder, which re-renders from a repaired graph:"
echo
$OCC plan demo-broken --env prod

rule "5. Emit the same plan in five languages"
for lang in terraform cloudformation pulumi-python cdk-typescript kubernetes; do
  printf '\033[36m%s\033[0m\n' "$lang"
  $OCC iac reference-web-service --env prod --language "$lang" 2>/dev/null | tail -n +2 | sed 's/^/    /'
done

rule "6. A slice of the generated Terraform"
$OCC iac reference-web-service --env prod --language terraform --show locals.tf

rule "7. SRE setup derived from the plan"
$OCC slo reference-web-service --env prod --runbook "$WORK/runbook.md"
echo
echo "Runbook written: $(wc -l < "$WORK/runbook.md") lines. First section:"
head -18 "$WORK/runbook.md" | sed 's/^/    /'

rule "8. Incident triage, and what an agent may do about it"
$OCC diagnose refsvc --signal "Task stopped: OOMKilled, exit code 137" --env dev --remediate
echo
echo "The same signal, in production:"
$OCC diagnose refsvc --signal "Task stopped: OOMKilled, exit code 137" --env prod --remediate
echo
echo "A signal nothing in the library matches:"
$OCC diagnose refsvc --signal "everything feels slow today" --env prod --remediate

rule "9. ChatOps"
$OCC chat plan reference-web-service --env prod
echo
$OCC chat approve iac-builder
echo
$OCC chat 'remediate refsvc --signal "CannotPullContainerError" --env prod'

rule "10. The audit trail"
$OCC logs --last 12

rule "Done"
cat <<'TXT'
Nothing above touched a cloud account, and nothing was applied. That is the
design, not a limitation of the demo: the framework has no apply path and
holds no credentials.

Next:
  make up                    service + OTel collector + Jaeger on :16686
  make test                  the full suite
  occ chat                   an interactive ChatOps session
TXT
