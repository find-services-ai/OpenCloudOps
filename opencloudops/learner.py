"""The learner: production lessons as executable checks, plus agent memory.

The framework this grew out of shipped its hard-won lessons as a Python dict
of prose (``bootstrap/LEARNINGS.py``) -- correct content that no agent could
act on, because nothing read it. Here each lesson keeps its prose *and* gets a
detector, so "we learned not to do this" becomes "the pipeline refuses to do
this".

Two halves:

``Lesson``  -- a durable, reviewed pattern with a detector that runs against
              emitted IaC. These ship with the framework.
``Memory``  -- something this installation learned the hard way: a failure, its
              root cause, and the guard that now prevents it. Written by the
              SRE agent at incident close, read by the knowledge curator before
              the next build. Stored as JSONL under ``memories/``.

Deliberately *not* an LLM. A lesson you can only apply by asking a model is a
lesson you will apply inconsistently.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field

from opencloudops.spec import PlatformContract, ResolvedPlan

MEMORY_DIR = Path(os.environ.get("OCC_MEMORY_DIR", "memories"))


class LessonSeverity(str, Enum):
    high = "HIGH"
    medium = "MEDIUM"
    low = "LOW"


class LessonHit(BaseModel):
    lesson_id: str
    severity: LessonSeverity
    artifact: str
    line: int | None = None
    excerpt: str = ""
    message: str
    remediation: str


#: One emitted artifact: its path and its text.
Artifact = tuple[str, str]
Detector = Callable[[str, str, ResolvedPlan, PlatformContract], list[LessonHit]]


class Lesson(BaseModel):
    """A durable lesson. ``pattern``/``anti_pattern``/``cost_of_deferral`` are
    carried verbatim from the original framework's LEARNINGS -- they are the
    reason the detector exists, and they are what a reviewer reads when the
    detector fires."""

    id: str
    severity: LessonSeverity
    pattern: str
    anti_pattern: str
    when_it_bites: str
    cost_of_deferral: str
    learned_from: str = ""
    applies_to: list[str] = Field(default_factory=lambda: ["*"])

    model_config = {"arbitrary_types_allowed": True}


LESSONS: dict[str, Lesson] = {}
DETECTORS: dict[str, Detector] = {}


def lesson(spec: Lesson) -> Callable[[Detector], Detector]:
    """Register a lesson together with the detector that enforces it.

    A lesson without a detector cannot be registered -- that is the whole
    correction being made to the original prose-only design.
    """

    def wrap(fn: Detector) -> Detector:
        LESSONS[spec.id] = spec
        DETECTORS[spec.id] = fn
        return fn

    return wrap


def _matches(artifact: str, globs: list[str]) -> bool:
    if "*" in globs:
        return True
    return any(artifact.endswith(suffix.lstrip("*")) for suffix in globs)


def _hits(
    lesson_id: str,
    artifact: str,
    content: str,
    regex: re.Pattern[str],
    message: str,
    remediation: str,
) -> list[LessonHit]:
    out: list[LessonHit] = []
    spec = LESSONS[lesson_id]
    for i, line in enumerate(content.splitlines(), start=1):
        if regex.search(line):
            out.append(
                LessonHit(
                    lesson_id=lesson_id,
                    severity=spec.severity,
                    artifact=artifact,
                    line=i,
                    excerpt=line.strip()[:160],
                    message=message,
                    remediation=remediation,
                )
            )
    return out


# ---------------------------------------------------------------------------
# lessons
# ---------------------------------------------------------------------------

_COUNT_RE = re.compile(r"^\s*count\s*=\s*length\(")


@lesson(
    Lesson(
        id="for-each-over-count",
        severity=LessonSeverity.high,
        pattern="Iterate a map with for_each, never count = length(list)",
        anti_pattern="count = length(var.services) -- removing one shifts every index after it",
        when_it_bites="When defining the compute orchestration pattern, before the first apply",
        cost_of_deferral="Adding or removing a service destroys unrelated resources on the next apply",
        learned_from="15-service ECS platform: one service removal replaced four others",
        applies_to=["*.tf"],
    )
)
def _for_each_over_count(
    artifact: str, content: str, _p: ResolvedPlan, _c: PlatformContract
) -> list[LessonHit]:
    if not _matches(artifact, LESSONS["for-each-over-count"].applies_to):
        return []
    return _hits(
        "for-each-over-count",
        artifact,
        content,
        _COUNT_RE,
        "count = length(...) makes resource addresses positional.",
        "Use for_each over a map keyed by a stable name.",
    )


_DECORATIVE_RE = re.compile(r"[=═─—_-]{12,}")


@lesson(
    Lesson(
        id="decorative-comments",
        severity=LessonSeverity.low,
        pattern="Comments state why, not where the section starts",
        anti_pattern="Long rules of =, box-drawing characters, or dashes as section dividers",
        when_it_bites="Every time an agent reads the file",
        cost_of_deferral="3K tokens per file of pure noise, on every agent read",
        learned_from="Agent context budget blown by comment art",
        applies_to=["*.tf", "*.py", "*.ts", "*.yaml"],
    )
)
def _decorative_comments(
    artifact: str, content: str, _p: ResolvedPlan, _c: PlatformContract
) -> list[LessonHit]:
    if not _matches(artifact, LESSONS["decorative-comments"].applies_to):
        return []
    out: list[LessonHit] = []
    for i, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()
        if not stripped.startswith(("#", "//", "/*", "*")):
            continue
        if _DECORATIVE_RE.search(stripped):
            out.append(
                LessonHit(
                    lesson_id="decorative-comments",
                    severity=LessonSeverity.low,
                    artifact=artifact,
                    line=i,
                    excerpt=stripped[:80],
                    message="Decorative comment rule -- tokens spent on no information.",
                    remediation="Delete the rule; keep the sentence that says why.",
                )
            )
    return out


_UNPINNED_MODULE_RE = re.compile(r'source\s*=\s*"(git::|github\.com|registry\.terraform\.io)')


@lesson(
    Lesson(
        id="module-interface-validation",
        severity=LessonSeverity.high,
        pattern="Pin every module to an immutable ref (?ref=vN or an exact version)",
        anti_pattern="Referencing a module's default branch and guessing its outputs",
        when_it_bites="Before writing the first module call",
        cost_of_deferral="Plan fails on 'output not found', or an upstream change lands unreviewed",
        learned_from="Module output returned a list where a string was assumed",
        applies_to=["*.tf"],
    )
)
def _module_pinning(
    artifact: str, content: str, _p: ResolvedPlan, _c: PlatformContract
) -> list[LessonHit]:
    if not _matches(artifact, LESSONS["module-interface-validation"].applies_to):
        return []
    out: list[LessonHit] = []
    for i, line in enumerate(content.splitlines(), start=1):
        if _UNPINNED_MODULE_RE.search(line) and "?ref=" not in line and "version" not in line:
            out.append(
                LessonHit(
                    lesson_id="module-interface-validation",
                    severity=LessonSeverity.high,
                    artifact=artifact,
                    line=i,
                    excerpt=line.strip()[:160],
                    message="Module source is not pinned to an immutable ref.",
                    remediation="Append ?ref=v<N>, or set an exact version constraint.",
                )
            )
    return out


@lesson(
    Lesson(
        id="naming-with-resource-type",
        severity=LessonSeverity.high,
        pattern="Resource names carry a resource-type token: {org}-{bu}-{env}-{service}-{component}-{abbrev}",
        anti_pattern="Dropping the type token, so a bucket and a queue can collide on one name",
        when_it_bites="Phase 0 -- the naming standard must be locked before the first resource",
        cost_of_deferral="Bulk rename of every resource; security group rules break mid-flight",
        learned_from="V1->V2 migration where names had to be recomputed across 28 references",
        applies_to=["*"],
    )
)
def _naming_carries_type(
    artifact: str, _content: str, plan: ResolvedPlan, platform: PlatformContract
) -> list[LessonHit]:
    # A plan-level lesson: checked once, against the first artifact only, so a
    # naming violation is reported once rather than once per emitted file.
    if not artifact.endswith("main.tf"):
        return []
    out: list[LessonHit] = []
    for node in plan.nodes:
        abbrev = platform.naming.abbreviations.get(node.type.value)
        if abbrev and abbrev not in node.name:
            out.append(
                LessonHit(
                    lesson_id="naming-with-resource-type",
                    severity=LessonSeverity.high,
                    artifact=artifact,
                    message=(
                        f"{node.id} is named {node.name!r}, which carries no "
                        f"resource-type token ({abbrev!r})."
                    ),
                    remediation="Keep {abbrev} in naming.template, or set a per-type override that includes it.",
                )
            )
    return out


@lesson(
    Lesson(
        id="env-leak",
        severity=LessonSeverity.high,
        pattern="An artifact for one environment names only that environment",
        anti_pattern="A production account id, region, or endpoint appearing in a dev artifact",
        when_it_bites="After any cross-environment merge or promotion",
        cost_of_deferral="A promotion silently points one environment at another's data",
        learned_from="Cross-env contamination caught only after apply",
        applies_to=["*"],
    )
)
def _env_leak(
    artifact: str, content: str, plan: ResolvedPlan, platform: PlatformContract
) -> list[LessonHit]:
    """The single most valuable check the original framework described and the
    one it could not run: other environments' tokens must not appear here."""
    others = [e for e in platform.environments if e.name != plan.environment]
    out: list[LessonHit] = []
    for env in others:
        # Match the env's short token only as a whole name segment, so 'prd'
        # inside 'prdct-catalog' is not a false positive.
        token_re = re.compile(rf"(?<![a-z0-9]){re.escape(env.short)}(?![a-z0-9])")
        region_re = re.compile(re.escape(env.region))
        for i, line in enumerate(content.splitlines(), start=1):
            hit = token_re.search(line) or (region_re.search(line) if env.region != plan.region else None)
            if hit:
                out.append(
                    LessonHit(
                        lesson_id="env-leak",
                        severity=LessonSeverity.high,
                        artifact=artifact,
                        line=i,
                        excerpt=line.strip()[:160],
                        message=(
                            f"artifact for {plan.environment!r} references environment "
                            f"{env.name!r} ({hit.group(0)!r})."
                        ),
                        remediation="Environment-specific values live in that environment's own tfvars, never inline.",
                    )
                )
    return out


_HARDCODED_SECRET_RE = re.compile(
    r'(password|secret|api[_-]?key|token)\s*[=:]\s*["\'](?!\$\{|var\.|\{\{)[^"\']{8,}["\']',
    re.IGNORECASE,
)


@lesson(
    Lesson(
        id="secrets-not-inline",
        severity=LessonSeverity.high,
        pattern="Credentials come from a secret store; IaC holds a reference, never a value",
        anti_pattern="A literal password or API key in a committed file",
        when_it_bites="When wiring service environment variables",
        cost_of_deferral="A secret in git history is a secret you must rotate, not delete",
        learned_from="Deterministic values put in Secrets Manager, real ones put in tfvars",
        applies_to=["*"],
    )
)
def _no_inline_secrets(
    artifact: str, content: str, _p: ResolvedPlan, _c: PlatformContract
) -> list[LessonHit]:
    return _hits(
        "secrets-not-inline",
        artifact,
        content,
        _HARDCODED_SECRET_RE,
        "Looks like a literal credential in an emitted artifact.",
        "Reference the secret store; never inline the value.",
    )


_WILDCARD_IAM_RE = re.compile(r'"(Action|Resource)"\s*:\s*"\*"|Action\s*=\s*\[?\s*"\*"')


@lesson(
    Lesson(
        id="no-wildcard-iam",
        severity=LessonSeverity.high,
        pattern="IAM policies name the actions and the resources they need",
        anti_pattern='Action: "*" or Resource: "*"',
        when_it_bites="Whenever an agent writes a policy document to make something work",
        cost_of_deferral="The blast radius of every later mistake grows to the whole account",
        learned_from="Least-privilege is the control the baseline claims; this is what proves it",
        applies_to=["*"],
    )
)
def _no_wildcard_iam(
    artifact: str, content: str, _p: ResolvedPlan, _c: PlatformContract
) -> list[LessonHit]:
    return _hits(
        "no-wildcard-iam",
        artifact,
        content,
        _WILDCARD_IAM_RE,
        "Wildcard IAM action or resource in an emitted policy.",
        "Enumerate the actions, and scope Resource to the ARNs this node owns.",
    )


_OPEN_CIDR_RE = re.compile(r'0\.0\.0\.0/0')


@lesson(
    Lesson(
        id="no-open-egress-in-prod",
        severity=LessonSeverity.high,
        pattern="Security group rules name a destination, not the whole internet",
        anti_pattern="0.0.0.0/0 in a production security group",
        when_it_bites="Whenever a rule is added to make connectivity work under time pressure",
        cost_of_deferral="Exfiltration path that no later review will notice",
        learned_from="'# TODO: Restrict' rules reaching production",
        applies_to=["*"],
    )
)
def _no_open_cidr(
    artifact: str, content: str, plan: ResolvedPlan, _c: PlatformContract
) -> list[LessonHit]:
    if not plan.production:
        return []
    return _hits(
        "no-open-egress-in-prod",
        artifact,
        content,
        _OPEN_CIDR_RE,
        "0.0.0.0/0 in a production artifact.",
        "Scope to a prefix list, a peer security group, or a VPC endpoint.",
    )


# ---------------------------------------------------------------------------
# junction questions -- asked by intake, before anything is generated
# ---------------------------------------------------------------------------

#: Questions that, asked early, remove most post-scaffold rework. Carried from
#: the original HIGH_VALUE_JUNCTION_QUESTIONS and each mapped to the field it
#: actually sets, so an answer changes the graph rather than a conversation.
JUNCTION_QUESTIONS: list[dict[str, str]] = [
    {
        "id": "data-classification",
        "question": "What is the most sensitive class of data this will hold?",
        "sets": "metadata.data_classification",
        "why": "Drives KMS, exposure rules, and whether restricted-data placement blocks the plan.",
    },
    {
        "id": "db-auth",
        "question": "How will services authenticate to the database -- IAM or a password?",
        "sets": "store.relational params / secret nodes",
        "why": "IAM auth removes a rotating secret entirely; a password adds one you must rotate.",
    },
    {
        "id": "shared-image",
        "question": "Do several services share one container image?",
        "sets": "compute.container_service grouping",
        "why": "Shared images must be released atomically or the group drifts apart.",
    },
    {
        "id": "public-surface",
        "question": "Is anything here reachable from the public internet?",
        "sets": "node.exposure",
        "why": "Public exposure demands an edge control; declaring it late means re-planning.",
    },
    {
        "id": "environments",
        "question": "Which environments does this need, and which is production?",
        "sets": "metadata.environments",
        "why": "Production flips redundancy, backup, and approval rules from warnings to blocks.",
    },
    {
        "id": "ticket",
        "question": "Which ticket authorises this work?",
        "sets": "governance.ticket",
        "why": "The governance gate refuses to route unticketed change.",
    },
]


# ---------------------------------------------------------------------------
# scanning + memory
# ---------------------------------------------------------------------------


class Memory(BaseModel):
    """Something this installation learned. Written at incident close."""

    ts: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    service: str
    environment: str
    symptom: str
    root_cause: str
    guard: str = Field(description="What now prevents a recurrence.")
    lesson_id: str | None = None
    request_id: str | None = None


class Learner:
    """Runs the lessons over emitted artifacts, and owns the memory store."""

    def __init__(self, memory_dir: Path | str | None = MEMORY_DIR) -> None:
        self.memory_dir = Path(memory_dir) if memory_dir is not None else None

    def scan(
        self,
        artifacts: dict[str, str],
        plan: ResolvedPlan,
        platform: PlatformContract,
    ) -> list[LessonHit]:
        hits: list[LessonHit] = []
        for artifact, content in sorted(artifacts.items()):
            for lesson_id, detector in DETECTORS.items():
                try:
                    hits.extend(detector(artifact, content, plan, platform))
                except Exception as e:  # a broken detector must not fail a build
                    hits.append(
                        LessonHit(
                            lesson_id=lesson_id,
                            severity=LessonSeverity.low,
                            artifact=artifact,
                            message=f"detector error: {e}",
                            remediation="Fix the detector; this is a framework bug, not your IaC.",
                        )
                    )
        severity_order = {LessonSeverity.high: 0, LessonSeverity.medium: 1, LessonSeverity.low: 2}
        hits.sort(key=lambda h: (severity_order[h.severity], h.lesson_id, h.artifact, h.line or 0))
        return hits

    def blocking(self, hits: list[LessonHit]) -> list[LessonHit]:
        return [h for h in hits if h.severity is LessonSeverity.high]

    # -- memory ---------------------------------------------------------

    def _path(self) -> Path | None:
        if self.memory_dir is None:
            return None
        return self.memory_dir / "incidents.jsonl"

    def record(self, memory: Memory) -> Memory:
        path = self._path()
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(memory.model_dump_json() + "\n")
        return memory

    def recall(self, service: str | None = None, limit: int = 20) -> list[Memory]:
        """What has already gone wrong here. Read before a build, so the same
        failure is not re-learned from scratch."""
        path = self._path()
        if path is None or not path.exists():
            return []
        out: list[Memory] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                m = Memory.model_validate_json(line)
            except Exception:
                continue
            if service is None or m.service == service:
                out.append(m)
        return out[-limit:]

    def catalog(self) -> list[Lesson]:
        return [LESSONS[k] for k in sorted(LESSONS)]
