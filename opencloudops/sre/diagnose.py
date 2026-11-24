"""Classify a production signal against the error-pattern library.

Deterministic matching, on purpose. A classifier whose answer changes between
two identical pages is worse than no classifier: on-call stops trusting it, and
an untrusted classifier is pure latency in an incident.

When nothing matches, this says so. A confident wrong answer during an incident
costs far more than an honest "unrecognised -- here is what to look at".
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from opencloudops.spec import ResolvedPlan, RiskTier
from opencloudops.sre.patterns import ERROR_PATTERNS, Confidence, ErrorPattern


class Diagnosis(BaseModel):
    service: str
    environment: str
    signal: str
    matched: bool
    pattern_id: str | None = None
    summary: str = ""
    root_cause: str = ""
    recommended_fix: str = ""
    confidence: Confidence = Confidence.low
    risk_tier: RiskTier = RiskTier.high
    auto_remediable: bool = False
    evidence: list[str] = Field(default_factory=list)
    affected_nodes: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)


def _affected(plan: ResolvedPlan | None, signal: str) -> list[str]:
    """Nodes whose id or resource name appears in the signal."""
    if plan is None:
        return []
    lowered = signal.lower()
    return [n.id for n in plan.nodes if n.id.lower() in lowered or n.name.lower() in lowered]


def _score(pattern: ErrorPattern, hits: list[str]) -> tuple[int, int]:
    """More indicators matched wins; ties break toward the more confident
    pattern, so an unambiguous signature is not shadowed by a vague one."""
    rank = {Confidence.high: 2, Confidence.medium: 1, Confidence.low: 0}
    return len(hits), rank[pattern.confidence]


def diagnose(
    signal: str,
    *,
    service: str,
    environment: str,
    plan: ResolvedPlan | None = None,
) -> Diagnosis:
    candidates: list[tuple[ErrorPattern, list[str]]] = []
    for pattern in ERROR_PATTERNS:
        hits = pattern.matches(signal)
        if hits:
            candidates.append((pattern, hits))

    if not candidates:
        return Diagnosis(
            service=service,
            environment=environment,
            signal=signal,
            matched=False,
            summary="No known failure shape matched this signal.",
            root_cause="Unrecognised. Treat as LOW confidence and escalate rather than guess.",
            recommended_fix="Escalate to a human with the raw signal attached.",
            confidence=Confidence.low,
            risk_tier=RiskTier.high,
            auto_remediable=False,
            affected_nodes=_affected(plan, signal),
            next_steps=[
                "Pull the first occurrence, not the most recent -- later lines are usually consequences.",
                "Check whether a deploy landed inside the window.",
                "If this recurs, add a pattern to opencloudops/sre/patterns.py so it is recognised next time.",
            ],
        )

    candidates.sort(key=lambda c: _score(*c), reverse=True)
    best, hits = candidates[0]
    others = [p.id for p, _ in candidates[1:]]

    next_steps = [best.recommended_fix]
    if not best.auto_remediable:
        reason = best.never_retry_reason or "This class of failure is not safe to fix automatically."
        next_steps.append(f"Not auto-remediable: {reason}")
    if best.confidence is Confidence.medium:
        next_steps.append("Fix once; if the same signal returns, escalate rather than loop.")

    return Diagnosis(
        service=service,
        environment=environment,
        signal=signal,
        matched=True,
        pattern_id=best.id,
        summary=best.summary,
        root_cause=best.root_cause,
        recommended_fix=best.recommended_fix,
        confidence=best.confidence,
        risk_tier=best.risk_tier,
        auto_remediable=best.auto_remediable,
        evidence=hits,
        affected_nodes=_affected(plan, signal),
        alternatives=others,
        next_steps=next_steps,
    )
