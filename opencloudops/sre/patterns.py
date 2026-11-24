"""The error-pattern library.

Carried forward from the framework this replaces, where it lived as a markdown
table an agent had to read and interpret. Here each pattern is data with a
compiled matcher, a confidence level, and -- crucially -- an explicit
``auto_remediable`` flag.

That flag encodes the hardest-won item on the list: some failures must never be
retried automatically, no matter how confidently they are classified. A
database connection failure during a failover looks exactly like one during an
outage, and an agent that "fixes" the first turns a 30-second blip into a
thundering herd. Confidence and permission are different questions, so they are
different fields.
"""

from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, Field

from opencloudops.spec import RiskTier


class Confidence(str, Enum):
    """How sure the classifier is that it identified the right failure.

    HIGH   -- the signature is unambiguous; fix and re-enter the loop.
    MEDIUM -- fix once, then escalate rather than loop.
    LOW    -- escalate immediately; a guess here costs more than a page.
    """

    high = "HIGH"
    medium = "MEDIUM"
    low = "LOW"


class ErrorPattern(BaseModel):
    id: str
    summary: str
    indicators: list[str] = Field(description="Regexes matched case-insensitively against the signal.")
    root_cause: str
    recommended_fix: str
    confidence: Confidence
    risk_tier: RiskTier
    auto_remediable: bool = Field(
        description="Whether an agent may apply the fix without a human, subject to the risk tier."
    )
    never_retry_reason: str = ""
    applies_to: list[str] = Field(default_factory=lambda: ["*"])

    def matches(self, signal: str) -> list[str]:
        """Return the indicators that fired, so evidence is reportable."""
        return [ind for ind in self.indicators if re.search(ind, signal, re.IGNORECASE)]


ERROR_PATTERNS: list[ErrorPattern] = [
    ErrorPattern(
        id="oom-kill",
        summary="Container or function killed for exceeding its memory limit",
        indicators=[r"OutOfMemoryError", r"OOMKilled", r"exit code 137", r"Runtime exited.*signal: killed"],
        root_cause="Memory ceiling is below the workload's real high-water mark, or the process leaks.",
        recommended_fix="Raise the memory limit once, within the platform ceiling, then profile before raising again.",
        confidence=Confidence.high,
        risk_tier=RiskTier.low,
        auto_remediable=True,
    ),
    ErrorPattern(
        id="connection-pool-exhaustion",
        summary="Application cannot acquire a database connection",
        indicators=[r"cannot acquire connection", r"pool timeout", r"connection pool exhausted", r"too many clients"],
        root_cause="Concurrency exceeds the pool, or connections are not being returned.",
        recommended_fix="Reduce concurrency or raise the pool size. Check for un-closed connections first.",
        confidence=Confidence.medium,
        risk_tier=RiskTier.medium,
        auto_remediable=False,
        never_retry_reason="Raising the pool against a saturated database makes the saturation worse.",
    ),
    ErrorPattern(
        id="database-unreachable",
        summary="Database endpoint not answering",
        indicators=[r"could not connect to server", r"connection refused.*5432", r"FATAL:.*the database system is starting up"],
        root_cause="Often a Multi-AZ failover in progress, not an outage.",
        recommended_fix="Wait one failover interval and re-check before changing anything.",
        confidence=Confidence.medium,
        risk_tier=RiskTier.high,
        auto_remediable=False,
        never_retry_reason=(
            "A failover and an outage look identical from the client. Automatic retry across a "
            "fleet converts a 30-second blip into a thundering herd."
        ),
    ),
    ErrorPattern(
        id="auth-failure",
        summary="Permission denied or credentials rejected",
        indicators=[r"AccessDenied", r"UnauthorizedOperation", r"\b401\b", r"\b403\b", r"InvalidSignatureException"],
        root_cause="IAM policy missing a required action, or a credential has expired or rotated.",
        recommended_fix="Compare the failing action against the node's policy. Rotation is a human action.",
        confidence=Confidence.medium,
        risk_tier=RiskTier.high,
        auto_remediable=False,
        never_retry_reason="Widening a policy to clear an error is how least privilege quietly disappears.",
    ),
    ErrorPattern(
        id="health-check-failure",
        summary="Load balancer or orchestrator reports the target unhealthy",
        indicators=[r"health ?check failed", r"unhealthy target", r"readiness probe failed", r"Task failed ELB health checks"],
        root_cause="The process is not serving the health path, or it is still starting.",
        recommended_fix="Check startup logs, then the health path and the start period. Raise the start period before raising resources.",
        confidence=Confidence.medium,
        risk_tier=RiskTier.low,
        auto_remediable=True,
    ),
    ErrorPattern(
        id="crash-loop",
        summary="Process restarts repeatedly during initialisation",
        indicators=[r"CrashLoopBackOff", r"Back-off restarting failed container", r"has restarted \d+ times"],
        root_cause="Initialisation fails -- configuration, a missing secret, or a failed migration.",
        recommended_fix="Read the first 50 lines of the first failed start. The later restarts are noise.",
        confidence=Confidence.medium,
        risk_tier=RiskTier.medium,
        auto_remediable=False,
    ),
    ErrorPattern(
        id="image-pull-failure",
        summary="Container image cannot be pulled",
        indicators=[r"CannotPullContainerError", r"ImagePullBackOff", r"ErrImagePull", r"manifest.*not found"],
        root_cause="The tag does not exist, or the registry denies the pull.",
        recommended_fix="Verify the tag exists and the pull role has access. Do not retry the same tag.",
        confidence=Confidence.high,
        risk_tier=RiskTier.medium,
        auto_remediable=False,
        never_retry_reason="Retrying a tag that does not exist cannot succeed and buries the real error.",
    ),
    ErrorPattern(
        id="queue-backlog",
        summary="Consumer is falling behind, or the dead-letter queue is growing",
        indicators=[r"ApproximateAgeOfOldestMessage", r"consumer lag", r"dlq depth", r"messages? in the dead[- ]letter"],
        root_cause="Processing is slower than arrival, or a poison message is being redriven.",
        recommended_fix="Check the DLQ first. A poison message looks like a throughput problem until you read one.",
        confidence=Confidence.medium,
        risk_tier=RiskTier.medium,
        auto_remediable=False,
    ),
    ErrorPattern(
        id="throttling",
        summary="Provider is throttling requests",
        indicators=[r"ThrottlingException", r"ProvisionedThroughputExceeded", r"Rate exceeded", r"TooManyRequests", r"\b429\b"],
        root_cause="Request rate is above the provisioned or account limit.",
        recommended_fix="Back off and retry with jitter. If sustained, raise provisioned capacity or request a limit increase.",
        confidence=Confidence.high,
        risk_tier=RiskTier.low,
        auto_remediable=True,
    ),
    ErrorPattern(
        id="dns-resolution",
        summary="Hostname does not resolve",
        indicators=[r"UnknownHostException", r"NXDOMAIN", r"Name or service not known", r"no such host"],
        root_cause="Endpoint is misconfigured, or VPC DNS resolution is off.",
        recommended_fix="Check the endpoint value in this environment's tfvars, then VPC DNS support.",
        confidence=Confidence.medium,
        risk_tier=RiskTier.medium,
        auto_remediable=False,
    ),
    ErrorPattern(
        id="tls-failure",
        summary="TLS handshake or certificate validation failed",
        indicators=[r"SSLHandshakeException", r"certificate.*expired", r"unable to verify the first certificate", r"x509:"],
        root_cause="Expired certificate, hostname mismatch, or a trust store missing the issuing CA.",
        recommended_fix="Check the certificate's expiry and SANs. Never disable verification to clear it.",
        confidence=Confidence.high,
        risk_tier=RiskTier.high,
        auto_remediable=False,
        never_retry_reason="The only 'automatic fix' for a TLS error is to stop verifying, which is not a fix.",
    ),
    ErrorPattern(
        id="disk-pressure",
        summary="No space left on device",
        indicators=[r"No space left on device", r"DiskPressure", r"disk quota exceeded"],
        root_cause="Logs or temporary files filling the volume.",
        recommended_fix="Add rotation or raise the volume. A read-only root filesystem with a bounded /tmp prevents recurrence.",
        confidence=Confidence.high,
        risk_tier=RiskTier.low,
        auto_remediable=True,
    ),
    ErrorPattern(
        id="deploy-rollback",
        summary="Deployment circuit breaker rolled the release back",
        indicators=[r"circuit breaker", r"deployment failed.*rolled back", r"ROLLBACK_IN_PROGRESS"],
        root_cause="The new revision never became healthy.",
        recommended_fix="The rollback already protected you. Diagnose the new revision before re-deploying it.",
        confidence=Confidence.high,
        risk_tier=RiskTier.medium,
        auto_remediable=False,
        never_retry_reason="Re-deploying the same artefact after a circuit-breaker rollback repeats the outage.",
    ),
]


def by_id(pattern_id: str) -> ErrorPattern:
    for p in ERROR_PATTERNS:
        if p.id == pattern_id:
            return p
    raise KeyError(f"no error pattern {pattern_id!r}")
