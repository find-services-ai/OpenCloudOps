"""Guarding: the policy engine.

Grounding limits *what* an agent may build. Guarding limits *how* it may be
configured, and it is the last thing that runs before any IaC is written.

Two rules make this worth having rather than decorative:

1. A ``BLOCK`` finding stops emission. ``GuardReport.blocked`` is checked by
   the IaC author agent, not merely reported to a human who might skim it.
2. Every field on ``SecurityBaseline`` has a check here. ``tests/test_guard.py``
   asserts that coverage, so adding a baseline field without a check fails CI
   instead of silently becoming a promise the framework does not keep.

Checks are ordinary Python functions registered in ``CHECKS``. They receive
the resolved plan and the platform contract, and yield findings. There is no
model in this path -- a policy decision an LLM could talk its way around is
not a policy.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable, Iterator
from enum import Enum

from pydantic import BaseModel, Field

from opencloudops.spec import (
    DataClassification,
    Exposure,
    NodeType,
    PlatformContract,
    ResolvedNode,
    ResolvedPlan,
    RiskTier,
)


class Severity(str, Enum):
    block = "BLOCK"
    warn = "WARN"
    info = "INFO"


class Finding(BaseModel):
    rule_id: str
    severity: Severity
    node_id: str | None = None
    message: str
    remediation: str = ""
    control: str = ""


class GuardReport(BaseModel):
    plan: str
    environment: str
    findings: list[Finding] = Field(default_factory=list)
    checks_run: int = 0

    @property
    def blocked(self) -> bool:
        return any(f.severity is Severity.block for f in self.findings)

    @property
    def blocks(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.block]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.warn]

    def risk_tier(self, platform: PlatformContract) -> RiskTier:
        """Risk of *applying* this plan, on the same ladder the SRE plane uses."""
        if self.blocked:
            return RiskTier.high
        env = platform.environment(self.environment)
        if env.production:
            return RiskTier.high
        if self.warnings:
            return RiskTier.medium
        return RiskTier.low

    def summary(self) -> str:
        if self.blocked:
            return f"BLOCKED -- {len(self.blocks)} blocking, {len(self.warnings)} warning(s)"
        if self.warnings:
            return f"PASS with {len(self.warnings)} warning(s)"
        return f"PASS -- {self.checks_run} checks, no findings"


Check = Callable[[ResolvedPlan, PlatformContract], Iterator[Finding]]
CHECKS: list[tuple[str, Check]] = []


def check(rule_id: str) -> Callable[[Check], Check]:
    def wrap(fn: Check) -> Check:
        CHECKS.append((rule_id, fn))
        return fn

    return wrap


def _severity(production: bool) -> Severity:
    """Outside production most baseline misses are warnings; in production
    they block. A dev environment that cannot be stood up is a framework
    nobody adopts, but that leniency must never reach prod."""
    return Severity.block if production else Severity.warn


# ---------------------------------------------------------------------------
# encryption
# ---------------------------------------------------------------------------


@check("SEC-001")
def encryption_at_rest(plan: ResolvedPlan, platform: PlatformContract) -> Iterator[Finding]:
    """baseline.encryption_at_rest_required"""
    if not platform.security.encryption_at_rest_required:
        return
    stateful = {NodeType.object_store, NodeType.table, NodeType.relational, NodeType.queue, NodeType.secret}
    for node in plan.nodes:
        if node.type not in stateful:
            continue
        if not any(c.startswith("encryption-at-rest") for c in node.controls):
            yield Finding(
                rule_id="SEC-001",
                severity=Severity.block,
                node_id=node.id,
                control="encryption-at-rest",
                message=(
                    f"{node.id} ({node.pattern_id}) stores data but its pattern declares no "
                    "encryption-at-rest control."
                ),
                remediation="Use a pattern whose controls include encryption-at-rest-kms.",
            )


@check("SEC-002")
def customer_managed_keys(plan: ResolvedPlan, platform: PlatformContract) -> Iterator[Finding]:
    """baseline.customer_managed_keys_in"""
    if plan.environment not in platform.security.customer_managed_keys_in:
        return
    for node in plan.nodes:
        if any(c.startswith("encryption-at-rest") for c in node.controls):
            if "encryption-at-rest-kms" not in node.controls:
                yield Finding(
                    rule_id="SEC-002",
                    severity=Severity.block,
                    node_id=node.id,
                    control="encryption-at-rest-kms",
                    message=(
                        f"{plan.environment} requires customer-managed keys, but {node.id} uses "
                        "provider-managed encryption."
                    ),
                    remediation="Switch to a pattern with encryption-at-rest-kms.",
                )


@check("SEC-003")
def encryption_in_transit(plan: ResolvedPlan, platform: PlatformContract) -> Iterator[Finding]:
    """baseline.encryption_in_transit_required"""
    if not platform.security.encryption_in_transit_required:
        return
    # A VPC has no transit setting of its own. Secrets Manager and CloudWatch
    # have no resource-level TLS toggle either -- their only endpoints are
    # TLS-only -- so requiring the control there would push patterns into
    # claiming something they do not render, which is worse than exempting it.
    no_transit_setting = {NodeType.network, NodeType.secret, NodeType.observability}
    for node in plan.nodes:
        if node.type in no_transit_setting:
            continue
        if "encryption-in-transit-tls" not in node.controls:
            yield Finding(
                rule_id="SEC-003",
                severity=_severity(plan.production),
                node_id=node.id,
                control="encryption-in-transit-tls",
                message=f"{node.id} ({node.pattern_id}) does not declare TLS in transit.",
                remediation="Use a TLS-enforcing pattern, or document the exception in the graph.",
            )


@check("SEC-004")
def min_tls_version(plan: ResolvedPlan, platform: PlatformContract) -> Iterator[Finding]:
    """baseline.min_tls_version -- reported once per plan, since it is rendered
    as a shared policy value rather than a per-resource setting."""
    required = platform.security.min_tls_version
    try:
        if float(required) < 1.2:
            yield Finding(
                rule_id="SEC-004",
                severity=Severity.block,
                message=(
                    f"platform.security.min_tls_version is {required}; TLS below 1.2 is "
                    "not an acceptable enterprise baseline."
                ),
                remediation="Set security.min_tls_version to 1.2 or higher.",
            )
    except ValueError:
        yield Finding(
            rule_id="SEC-004",
            severity=Severity.block,
            message=f"security.min_tls_version={required!r} is not a version number.",
            remediation="Use a numeric value such as '1.2'.",
        )


# ---------------------------------------------------------------------------
# exposure and networking
# ---------------------------------------------------------------------------


@check("NET-001")
def no_public_object_storage(plan: ResolvedPlan, platform: PlatformContract) -> Iterator[Finding]:
    """baseline.deny_public_object_storage"""
    if not platform.security.deny_public_object_storage:
        return
    for node in plan.by_type(NodeType.object_store):
        if node.exposure is Exposure.public or "block-public-access" not in node.controls:
            yield Finding(
                rule_id="NET-001",
                severity=Severity.block,
                node_id=node.id,
                control="block-public-access",
                message=f"{node.id} is object storage that is public or lacks block-public-access.",
                remediation=(
                    "Serve public objects through a CDN in front of a private bucket; "
                    "never expose the bucket itself."
                ),
            )


@check("NET-002")
def no_public_database(plan: ResolvedPlan, platform: PlatformContract) -> Iterator[Finding]:
    """baseline.deny_public_database"""
    if not platform.security.deny_public_database:
        return
    for node in plan.nodes:
        if node.type in {NodeType.relational, NodeType.table} and node.exposure is Exposure.public:
            yield Finding(
                rule_id="NET-002",
                severity=Severity.block,
                node_id=node.id,
                message=f"{node.id} is a datastore declared with public exposure.",
                remediation="Set exposure to private and reach it from compute inside the VPC.",
            )


@check("NET-003")
def private_networking_required(plan: ResolvedPlan, platform: PlatformContract) -> Iterator[Finding]:
    """baseline.require_private_networking_for"""
    for node_type in platform.security.require_private_networking_for:
        for node in plan.by_type(node_type):
            if "private-subnets-only" not in node.controls:
                yield Finding(
                    rule_id="NET-003",
                    severity=_severity(plan.production),
                    node_id=node.id,
                    control="private-subnets-only",
                    message=(
                        f"{node.id} is a {node_type.value} but its pattern does not guarantee "
                        "private subnet placement."
                    ),
                    remediation="Choose a pattern with the private-subnets-only control.",
                )


@check("NET-004")
def flow_logs(plan: ResolvedPlan, platform: PlatformContract) -> Iterator[Finding]:
    """baseline.require_flow_logs"""
    if not platform.security.require_flow_logs:
        return
    for node in plan.by_type(NodeType.network):
        if "flow-logs-enabled" not in node.controls:
            yield Finding(
                rule_id="NET-004",
                severity=Severity.block,
                node_id=node.id,
                control="flow-logs-enabled",
                message=f"network {node.id} does not enable VPC flow logs.",
                remediation="Use a VPC pattern with flow-logs-enabled.",
            )


@check("NET-005")
def public_exposure_needs_edge_control(plan: ResolvedPlan, _: PlatformContract) -> Iterator[Finding]:
    """A node may be public only behind an inspected edge (WAF/CDN)."""
    for node in plan.nodes:
        if node.exposure is not Exposure.public:
            continue
        if not any(c in node.controls for c in ("waf-fronted", "cdn-fronted")):
            yield Finding(
                rule_id="NET-005",
                severity=_severity(plan.production),
                node_id=node.id,
                message=(
                    f"{node.id} claims public exposure without a waf-fronted or cdn-fronted "
                    "control on its pattern."
                ),
                remediation="Front it with a WAF/CDN pattern, or set exposure to internal.",
            )


@check("NET-006")
def private_cidr_only(plan: ResolvedPlan, _: PlatformContract) -> Iterator[Finding]:
    """A VPC CIDR must be RFC1918 space -- a public range here is almost always
    a typo, and it is unrecoverable once peered."""
    for node in plan.by_type(NodeType.network):
        cidr = node.params.get("cidr")
        if not cidr:
            continue
        try:
            net = ipaddress.ip_network(str(cidr), strict=False)
        except ValueError:
            yield Finding(
                rule_id="NET-006",
                severity=Severity.block,
                node_id=node.id,
                message=f"{node.id} has an unparseable CIDR {cidr!r}.",
                remediation="Use a valid RFC1918 CIDR such as 10.0.0.0/16.",
            )
            continue
        if not net.is_private:
            yield Finding(
                rule_id="NET-006",
                severity=Severity.block,
                node_id=node.id,
                message=f"{node.id} uses publicly routable CIDR {cidr}.",
                remediation="Use RFC1918 space (10/8, 172.16/12, 192.168/16).",
            )


# ---------------------------------------------------------------------------
# resilience
# ---------------------------------------------------------------------------


@check("RES-001")
def backups_required(plan: ResolvedPlan, platform: PlatformContract) -> Iterator[Finding]:
    """baseline.require_backups_in"""
    if plan.environment not in platform.security.require_backups_in:
        return
    for node in plan.nodes:
        if node.type in {NodeType.relational, NodeType.table} and "backups-enabled" not in node.controls:
            yield Finding(
                rule_id="RES-001",
                severity=Severity.block,
                node_id=node.id,
                control="backups-enabled",
                message=f"{node.id} holds state in {plan.environment} with no backup control.",
                remediation="Use a pattern with point-in-time recovery or automated backups.",
            )


@check("RES-002")
def production_redundancy(plan: ResolvedPlan, _: PlatformContract) -> Iterator[Finding]:
    """Single points of failure that only matter in production."""
    if not plan.production:
        return
    for node in plan.nodes:
        if node.params.get("single_nat_gateway") is True:
            yield Finding(
                rule_id="RES-002",
                severity=Severity.block,
                node_id=node.id,
                message=f"{node.id} uses a single NAT gateway in production.",
                remediation="Set single_nat_gateway: false so each AZ has its own egress path.",
            )
        if node.type is NodeType.relational and node.params.get("multi_az") is False:
            yield Finding(
                rule_id="RES-002",
                severity=Severity.block,
                node_id=node.id,
                message=f"{node.id} is a single-AZ database in production.",
                remediation="Set multi_az: true.",
            )
        if node.type is NodeType.container_service and int(node.params.get("desired_count", 0)) < 2:
            yield Finding(
                rule_id="RES-002",
                severity=Severity.block,
                node_id=node.id,
                message=f"{node.id} runs a single task in production.",
                remediation="Set desired_count to 2 or more.",
            )


@check("RES-003")
def observability_present(plan: ResolvedPlan, platform: PlatformContract) -> Iterator[Finding]:
    """baseline.require_observability_node -- you cannot run what you cannot see."""
    if not platform.security.require_observability_node:
        return
    if not plan.by_type(NodeType.observability):
        yield Finding(
            rule_id="RES-003",
            severity=_severity(plan.production),
            message=(
                f"plan {plan.graph_name!r} has no observability.stack node, so it ships with "
                "no log retention, dashboard, or alarms."
            ),
            remediation="Add an observability.stack node and depend it on the service nodes.",
        )


@check("RES-004")
def deletion_protection(plan: ResolvedPlan, _: PlatformContract) -> Iterator[Finding]:
    if not plan.production:
        return
    for node in plan.by_type(NodeType.relational):
        if "deletion-protection" not in node.controls:
            yield Finding(
                rule_id="RES-004",
                severity=Severity.block,
                node_id=node.id,
                control="deletion-protection",
                message=f"production database {node.id} has no deletion protection.",
                remediation="Use a pattern that sets deletion_protection.",
            )


# ---------------------------------------------------------------------------
# identity, secrets, governance
# ---------------------------------------------------------------------------


@check("IAM-001")
def least_privilege(plan: ResolvedPlan, platform: PlatformContract) -> Iterator[Finding]:
    """baseline.forbid_wildcard_iam -- enforced at the pattern level, because a
    pattern is the only thing that renders a policy document."""
    if not platform.security.forbid_wildcard_iam:
        return
    needs_identity = {
        NodeType.function,
        NodeType.container_service,
        NodeType.table,
        NodeType.queue,
        NodeType.secret,
    }
    for node in plan.nodes:
        if node.type in needs_identity and "least-privilege-iam" not in node.controls:
            yield Finding(
                rule_id="IAM-001",
                severity=_severity(plan.production),
                node_id=node.id,
                control="least-privilege-iam",
                message=f"{node.id} assumes an identity but its pattern makes no least-privilege claim.",
                remediation="Use a pattern that scopes its IAM policy to named resources.",
            )


@check("SEC-005")
def secret_rotation(plan: ResolvedPlan, platform: PlatformContract) -> Iterator[Finding]:
    """baseline.max_secret_rotation_days"""
    limit = platform.security.max_secret_rotation_days
    for node in plan.by_type(NodeType.secret):
        if "secret-rotation" not in node.controls:
            yield Finding(
                rule_id="SEC-005",
                severity=Severity.block,
                node_id=node.id,
                control="secret-rotation",
                message=f"{node.id} is a secret with no rotation control.",
                remediation="Use a pattern that configures automatic rotation.",
            )
            continue
        days = int(node.params.get("rotation_days", limit))
        if days > limit:
            yield Finding(
                rule_id="SEC-005",
                severity=Severity.block,
                node_id=node.id,
                message=f"{node.id} rotates every {days} days; the baseline maximum is {limit}.",
                remediation=f"Set rotation_days to {limit} or less.",
            )


@check("GOV-001")
def audit_logging(plan: ResolvedPlan, platform: PlatformContract) -> Iterator[Finding]:
    """baseline.require_audit_logging -- at least one node must produce an audit trail."""
    if not platform.security.require_audit_logging:
        return
    if not any("audit-logging-enabled" in n.controls for n in plan.nodes):
        yield Finding(
            rule_id="GOV-001",
            severity=Severity.block,
            message=f"no node in {plan.graph_name!r} declares audit-logging-enabled.",
            remediation="Add an observability.stack node, or use patterns that log access.",
        )


@check("GOV-002")
def region_allowlist(plan: ResolvedPlan, platform: PlatformContract) -> Iterator[Finding]:
    """baseline.allowed_regions -- data residency. Empty means unrestricted."""
    allowed = platform.security.allowed_regions
    if allowed and plan.region not in allowed:
        yield Finding(
            rule_id="GOV-002",
            severity=Severity.block,
            message=(
                f"environment {plan.environment!r} deploys to {plan.region}, which is not in "
                f"security.allowed_regions ({', '.join(allowed)})."
            ),
            remediation="Move the environment to an allowed region, or amend the allowlist deliberately.",
        )


@check("GOV-003")
def tagging_complete(plan: ResolvedPlan, platform: PlatformContract) -> Iterator[Finding]:
    for node in plan.nodes:
        missing = [t for t in platform.tagging.required if not node.tags.get(t)]
        if missing:
            yield Finding(
                rule_id="GOV-003",
                severity=Severity.block,
                node_id=node.id,
                message=f"{node.id} is missing required tag(s): {', '.join(missing)}.",
                remediation="Supply the value in the graph metadata or tagging.defaults.",
            )


@check("GOV-006")
def unreplaced_placeholders(plan: ResolvedPlan, _: PlatformContract) -> Iterator[Finding]:
    """No tag value may still be a placeholder.

    Discovery cannot infer an owner or a cost centre from a repository, so it
    writes `REPLACE-...` and relies on this check to stop the draft from
    reaching a cloud account. Without it the placeholder is a non-empty string
    that the tagging check happily accepts -- a promise in a comment rather
    than an enforced rule. It catches the copy-pasted graph too.
    """
    for node in plan.nodes:
        for key, value in sorted(node.tags.items()):
            if value.strip().upper().startswith("REPLACE"):
                yield Finding(
                    rule_id="GOV-006",
                    severity=Severity.block,
                    node_id=node.id,
                    message=f"{node.id} tag {key}={value!r} is still a placeholder.",
                    remediation=(
                        "Fill in the real value in the graph's metadata. Discovery leaves "
                        "owner and cost centre blank on purpose -- it cannot know them."
                    ),
                )


@check("GOV-004")
def restricted_data_placement(plan: ResolvedPlan, _: PlatformContract) -> Iterator[Finding]:
    """Restricted data may not sit in a node reachable from the internet, no
    matter what controls front it."""
    for node in plan.nodes:
        if node.data_classification is DataClassification.restricted and node.exposure is Exposure.public:
            yield Finding(
                rule_id="GOV-004",
                severity=Severity.block,
                node_id=node.id,
                message=f"{node.id} holds restricted data and is publicly exposed.",
                remediation="Restricted data is never directly internet-reachable. Put a service in front of it.",
            )


@check("GOV-005")
def oversized_function(plan: ResolvedPlan, _: PlatformContract) -> Iterator[Finding]:
    for node in plan.by_type(NodeType.function):
        memory = int(node.params.get("memory_mb", 0))
        if memory > 3008:
            yield Finding(
                rule_id="GOV-005",
                severity=Severity.warn,
                node_id=node.id,
                message=f"{node.id} requests {memory}MB. Past ~3GB a function is usually the wrong shape.",
                remediation="Consider compute.container_service, or profile the workload first.",
            )


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def evaluate(plan: ResolvedPlan, platform: PlatformContract) -> GuardReport:
    """Run every registered check against a resolved plan."""
    findings: list[Finding] = []
    for _rule_id, fn in CHECKS:
        findings.extend(fn(plan, platform))
    order = {Severity.block: 0, Severity.warn: 1, Severity.info: 2}
    findings.sort(key=lambda f: (order[f.severity], f.rule_id, f.node_id or ""))
    return GuardReport(
        plan=plan.graph_name,
        environment=plan.environment,
        findings=findings,
        checks_run=len(CHECKS),
    )


def covered_baseline_fields() -> set[str]:
    """Baseline fields a check's docstring claims to enforce.

    ``tests/test_guard.py`` compares this against ``SecurityBaseline``'s field
    list, so a new baseline setting cannot ship without a check.
    """
    covered: set[str] = set()
    for _rule_id, fn in CHECKS:
        doc = fn.__doc__ or ""
        for token in doc.split():
            if token.startswith("baseline."):
                covered.add(token.split(".", 1)[1].rstrip(".,"))
    return covered
