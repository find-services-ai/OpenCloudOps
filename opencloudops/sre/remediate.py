"""Turn a diagnosis into a remediation proposal.

The word is *proposal*. Nothing in this module changes anything: it produces a
described action, a risk tier, and a verdict on whether an agent is permitted
to carry it out under the platform's autonomy ladder.

Three separate gates have to agree before ``autonomous`` comes back true:

1. the pattern says the failure class is auto-remediable at all,
2. the risk tier is within the platform's autonomy ceiling,
3. the environment is not production.

Production is excluded unconditionally. The ladder already usually excludes it,
but relying on configuration for that would make a one-line ``platform.yaml``
edit enough to hand an agent production.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from opencloudops.spec import PlatformContract, ResolvedPlan, RiskTier
from opencloudops.sre.diagnose import Diagnosis
from opencloudops.sre.patterns import by_id

_TIER_ORDER = {RiskTier.low: 0, RiskTier.medium: 1, RiskTier.high: 2}

#: What a proposal actually does, per pattern. Values are descriptions plus the
#: change they imply, never commands to run -- an executable string here would
#: be one bug away from being executed.
_ACTIONS: dict[str, dict[str, str]] = {
    "oom-kill": {
        "action": "Raise the node's memory ceiling by one step",
        "change": "memory_mb: increase to the next size in the pattern's allowed values",
        "verify": "No OOM in the 30 minutes after the new revision is stable",
    },
    "health-check-failure": {
        "action": "Lengthen the health-check start period before touching resources",
        "change": "health check startPeriod: 20s -> 60s",
        "verify": "Target reaches healthy without a restart",
    },
    "throttling": {
        "action": "Add exponential backoff with jitter at the call site, then review capacity",
        "change": "client retry policy; provisioned capacity only if the rate is sustained",
        "verify": "Throttle count returns to baseline over one hour",
    },
    "disk-pressure": {
        "action": "Bound the writable volume and add log rotation",
        "change": "ephemeral storage sizeLimit; log retention on the group",
        "verify": "Volume utilisation stays below 70% over one stability window",
    },
}


class RemediationProposal(BaseModel):
    service: str
    environment: str
    pattern_id: str | None
    action: str
    change: str
    verification: str
    risk_tier: RiskTier
    autonomous: bool
    approval_required: bool
    approvers: list[str] = Field(default_factory=list)
    rationale: str
    blocked_reason: str = ""
    rollback: str = ""


def propose(
    diagnosis: Diagnosis,
    platform: PlatformContract,
    plan: ResolvedPlan | None = None,
) -> RemediationProposal:
    env = platform.environment(diagnosis.environment)
    ceiling = platform.autonomy.autonomous_up_to

    if not diagnosis.matched or diagnosis.pattern_id is None:
        return RemediationProposal(
            service=diagnosis.service,
            environment=diagnosis.environment,
            pattern_id=None,
            action="Escalate to a human with the raw signal",
            change="none",
            verification="n/a",
            risk_tier=RiskTier.high,
            autonomous=False,
            approval_required=True,
            approvers=env.approvers,
            rationale="The signal matched no known failure shape, so any change would be a guess.",
            blocked_reason="unrecognised signal",
        )

    pattern = by_id(diagnosis.pattern_id)
    action = _ACTIONS.get(
        pattern.id,
        {
            "action": pattern.recommended_fix,
            "change": "described only -- this pattern has no parameterised change",
            "verify": "Signal does not recur within one stability window",
        },
    )

    reasons: list[str] = []
    autonomous = True

    if not pattern.auto_remediable:
        autonomous = False
        reasons.append(
            pattern.never_retry_reason or "this failure class is never remediated automatically"
        )
    if _TIER_ORDER[pattern.risk_tier] > _TIER_ORDER[ceiling]:
        autonomous = False
        reasons.append(
            f"risk tier {pattern.risk_tier.value} exceeds the autonomy ceiling {ceiling.value}"
        )
    if env.production:
        autonomous = False
        reasons.append(
            "production is excluded from autonomous remediation regardless of tier or ladder"
        )

    return RemediationProposal(
        service=diagnosis.service,
        environment=diagnosis.environment,
        pattern_id=pattern.id,
        action=action["action"],
        change=action["change"],
        verification=action["verify"],
        risk_tier=pattern.risk_tier,
        autonomous=autonomous,
        approval_required=not autonomous,
        approvers=env.approvers,
        rationale=(
            f"{pattern.summary}. Root cause: {pattern.root_cause} "
            f"Classified at {pattern.confidence.value} confidence from "
            f"{len(diagnosis.evidence)} matching indicator(s)."
        ),
        blocked_reason="; ".join(reasons),
        rollback=(
            "Revert the parameter to its prior value and redeploy. The change is a single "
            "field in the resource graph, so the rollback is the inverse edit."
        ),
    )
