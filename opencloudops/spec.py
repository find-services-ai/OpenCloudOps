"""Typed contracts for OpenCloudOps.

Three documents drive everything in this framework:

1. ``platform.yaml``      -- the *platform contract*: who the org is, what
   environments exist, how things are named, what tags are mandatory, and
   what the security baseline refuses to bend on.
2. ``*.graph.yaml``       -- a *resource graph*: the abstract shape of an
   application (nodes + edges), written without provider nouns.
3. ``patterns/*.yaml``    -- the *pattern catalog*: the only concrete
   infrastructure this framework is allowed to emit.

Agents may only ever combine these three. They may not invent a resource
that has no pattern, a name that ignores the naming template, or a control
the baseline forbids. That is the whole point: the models below are the
grounding surface, and ``guard.py`` is the guarding surface.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# shared vocabulary
# ---------------------------------------------------------------------------

SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SHORT_RE = re.compile(r"^[a-z0-9]{2,12}$")


class Provider(str, Enum):
    aws = "aws"
    azure = "azure"
    gcp = "gcp"
    kubernetes = "kubernetes"


class DataClassification(str, Enum):
    public = "public"
    internal = "internal"
    confidential = "confidential"
    restricted = "restricted"


class Exposure(str, Enum):
    """How reachable a node is meant to be. ``public`` is the only value the
    guard treats as an internet-facing claim, and it demands an edge control."""

    private = "private"
    internal = "internal"
    public = "public"


class Relation(str, Enum):
    depends_on = "depends_on"
    reads = "reads"
    writes = "writes"
    invokes = "invokes"
    publishes = "publishes"
    consumes = "consumes"


class RiskTier(str, Enum):
    """Mirrors the human-in-the-loop ladder used by the SRE plane.

    LOW may be applied by an agent on its own, MEDIUM needs a recorded
    approval, HIGH is never applied by an agent under any flag.
    """

    low = "LOW"
    medium = "MEDIUM"
    high = "HIGH"


class NodeType(str, Enum):
    """Provider-neutral node kinds. Every value here must have at least one
    approved pattern in the catalog or the architect agent cannot use it."""

    network = "net.vpc"
    function = "compute.function"
    container_service = "compute.container_service"
    object_store = "store.object"
    table = "store.table"
    relational = "store.relational"
    queue = "queue.standard"
    secret = "secret.string"
    observability = "observability.stack"


# ---------------------------------------------------------------------------
# 1. platform contract
# ---------------------------------------------------------------------------


class EnvironmentSpec(BaseModel):
    name: str
    short: str = Field(description="Token used inside resource names, e.g. 'prd'.")
    region: str
    production: bool = False
    approvers: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not SLUG_RE.match(v):
            raise ValueError(f"environment name must be kebab-case, got {v!r}")
        return v

    @field_validator("short")
    @classmethod
    def _short(cls, v: str) -> str:
        if not SHORT_RE.match(v):
            raise ValueError(f"environment short token must be 2-12 [a-z0-9], got {v!r}")
        return v


class NamingSpec(BaseModel):
    """Enterprise naming. One template, plus per-node-type overrides for the
    resources whose provider rules make the default template illegal
    (S3 bucket global uniqueness, IAM 64-char limits, and so on)."""

    template: str = "{org}-{bu}-{env}-{service}-{component}-{abbrev}"
    separator: str = "-"
    max_length: int = 63
    lowercase: bool = True
    overrides: dict[str, str] = Field(default_factory=dict)
    abbreviations: dict[str, str] = Field(
        default_factory=lambda: {
            "net.vpc": "vpc",
            "compute.function": "fn",
            "compute.container_service": "svc",
            "store.object": "s3b",
            "store.table": "tbl",
            "store.relational": "rds",
            "queue.standard": "sqs",
            "secret.string": "sec",
            "observability.stack": "obs",
        }
    )

    @field_validator("template")
    @classmethod
    def _has_required_tokens(cls, v: str) -> str:
        required = {"org", "env", "service"}
        present = set(re.findall(r"\{(\w+)\}", v))
        missing = required - present
        if missing:
            raise ValueError(
                "naming.template must include " + ", ".join(f"{{{m}}}" for m in sorted(missing))
            )
        return v


class TaggingSpec(BaseModel):
    required: list[str] = Field(
        default_factory=lambda: [
            "Org",
            "BusinessUnit",
            "Environment",
            "Service",
            "Owner",
            "CostCenter",
            "DataClassification",
            "ManagedBy",
        ]
    )
    defaults: dict[str, str] = Field(default_factory=lambda: {"ManagedBy": "opencloudops"})


class SecurityBaseline(BaseModel):
    """The non-negotiables. ``guard.py`` turns every field here into a check;
    a field with no check is a lie, so the guard test asserts coverage."""

    encryption_at_rest_required: bool = True
    encryption_in_transit_required: bool = True
    customer_managed_keys_in: list[str] = Field(default_factory=lambda: ["prod"])
    deny_public_object_storage: bool = True
    deny_public_database: bool = True
    require_private_networking_for: list[NodeType] = Field(
        default_factory=lambda: [NodeType.relational, NodeType.container_service]
    )
    require_flow_logs: bool = True
    require_audit_logging: bool = True
    require_backups_in: list[str] = Field(default_factory=lambda: ["prod"])
    min_tls_version: str = "1.2"
    max_secret_rotation_days: int = 90
    allowed_regions: list[str] = Field(default_factory=list)
    forbid_wildcard_iam: bool = True
    require_observability_node: bool = True


class AutonomySpec(BaseModel):
    """Which risk tier an agent may act on without a human."""

    autonomous_up_to: RiskTier = RiskTier.low
    approval_required_for: list[RiskTier] = Field(default_factory=lambda: [RiskTier.medium])
    human_only: list[RiskTier] = Field(default_factory=lambda: [RiskTier.high])
    veto_window_minutes: int = 30


class PlatformContract(BaseModel):
    org: str
    business_unit: str
    default_provider: Provider = Provider.aws
    environments: list[EnvironmentSpec]
    naming: NamingSpec = Field(default_factory=NamingSpec)
    tagging: TaggingSpec = Field(default_factory=TaggingSpec)
    security: SecurityBaseline = Field(default_factory=SecurityBaseline)
    autonomy: AutonomySpec = Field(default_factory=AutonomySpec)

    @field_validator("org", "business_unit")
    @classmethod
    def _short_token(cls, v: str) -> str:
        if not SHORT_RE.match(v):
            raise ValueError(f"must be 2-12 chars of [a-z0-9], got {v!r}")
        return v

    @model_validator(mode="after")
    def _envs_unique(self) -> PlatformContract:
        names = [e.name for e in self.environments]
        if len(names) != len(set(names)):
            raise ValueError("environment names must be unique")
        shorts = [e.short for e in self.environments]
        if len(shorts) != len(set(shorts)):
            raise ValueError("environment short tokens must be unique")
        if not self.environments:
            raise ValueError("at least one environment is required")
        return self

    def environment(self, name: str) -> EnvironmentSpec:
        for e in self.environments:
            if e.name == name:
                return e
        known = ", ".join(e.name for e in self.environments)
        raise KeyError(f"environment {name!r} is not declared in the platform contract (have: {known})")


# ---------------------------------------------------------------------------
# 2. resource graph
# ---------------------------------------------------------------------------


class GraphNode(BaseModel):
    id: str
    type: NodeType
    pattern: str | None = Field(
        default=None,
        description="Explicit pattern id. Omitted means 'resolve from the catalog'.",
    )
    description: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    exposure: Exposure = Exposure.private
    data_classification: DataClassification | None = None
    environments: list[str] | None = Field(
        default=None, description="Restrict this node to a subset of the graph's environments."
    )

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not SLUG_RE.match(v):
            raise ValueError(f"node id must be kebab-case, got {v!r}")
        return v


class GraphEdge(BaseModel):
    from_: str = Field(alias="from")
    to: str
    relation: Relation = Relation.depends_on

    model_config = {"populate_by_name": True}


class GraphMetadata(BaseModel):
    name: str
    service: str
    owner: str
    cost_center: str
    data_classification: DataClassification = DataClassification.internal
    environments: list[str] = Field(default_factory=lambda: ["dev"])
    source_idea: str = ""

    @field_validator("name")
    @classmethod
    def _name_slug(cls, v: str) -> str:
        if not SLUG_RE.match(v):
            raise ValueError(f"graph name must be kebab-case, got {v!r}")
        return v

    @field_validator("service")
    @classmethod
    def _service_short(cls, v: str) -> str:
        if not SHORT_RE.match(v):
            raise ValueError(
                f"service must be a 2-12 char [a-z0-9] token -- it goes inside every resource name, got {v!r}"
            )
        return v


class GraphSpecBody(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge] = Field(default_factory=list)


class ResourceGraph(BaseModel):
    apiVersion: str = "opencloudops.dev/v1"
    kind: str = "ResourceGraph"
    metadata: GraphMetadata
    spec: GraphSpecBody

    @model_validator(mode="after")
    def _structurally_sound(self) -> ResourceGraph:
        if self.kind != "ResourceGraph":
            raise ValueError(f"kind must be 'ResourceGraph', got {self.kind!r}")
        ids = [n.id for n in self.spec.nodes]
        if len(ids) != len(set(ids)):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"duplicate node ids: {', '.join(dupes)}")
        if not ids:
            raise ValueError("a resource graph needs at least one node")
        known = set(ids)
        for e in self.spec.edges:
            if e.from_ not in known:
                raise ValueError(f"edge references unknown node {e.from_!r}")
            if e.to not in known:
                raise ValueError(f"edge references unknown node {e.to!r}")
            if e.from_ == e.to:
                raise ValueError(f"self-edge on node {e.to!r}")
        return self

    def node(self, node_id: str) -> GraphNode:
        for n in self.spec.nodes:
            if n.id == node_id:
                return n
        raise KeyError(f"no node {node_id!r} in graph {self.metadata.name!r}")


# ---------------------------------------------------------------------------
# 3. pattern catalog
# ---------------------------------------------------------------------------


class PatternStatus(str, Enum):
    approved = "approved"
    experimental = "experimental"
    deprecated = "deprecated"


class ParamSpec(BaseModel):
    type: str = "string"
    default: Any = None
    required: bool = False
    allowed: list[Any] | None = None
    description: str = ""


class SLODefaults(BaseModel):
    availability_pct: float = 99.9
    latency_p95_ms: int | None = None
    error_budget_window_days: int = 30
    freshness_minutes: int | None = None


class Pattern(BaseModel):
    """One approved way to build one node type on one provider.

    The emitters key off ``node_type``; ``controls`` is what the guard reads
    to decide whether the baseline is satisfied without re-deriving provider
    trivia in every check.
    """

    id: str
    provider: Provider
    node_type: NodeType
    summary: str
    status: PatternStatus = PatternStatus.approved
    controls: list[str] = Field(default_factory=list)
    params: dict[str, ParamSpec] = Field(default_factory=dict)
    slo: SLODefaults = Field(default_factory=SLODefaults)
    emits: list[str] = Field(
        default_factory=list, description="Concrete resource kinds this pattern renders."
    )
    notes: str = ""

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not SLUG_RE.match(v):
            raise ValueError(f"pattern id must be kebab-case, got {v!r}")
        return v


# ---------------------------------------------------------------------------
# 4. resolved plan -- what agents hand to emitters
# ---------------------------------------------------------------------------


class ResolvedNode(BaseModel):
    """A graph node after grounding (pattern resolved) and naming (name
    computed). Emitters read only this; they never see raw graph nodes, so
    an unnamed or ungrounded node cannot reach an emitter."""

    id: str
    type: NodeType
    pattern_id: str
    provider: Provider
    name: str
    params: dict[str, Any]
    tags: dict[str, str]
    controls: list[str]
    exposure: Exposure
    data_classification: DataClassification
    depends_on: list[str] = Field(default_factory=list)
    slo: SLODefaults = Field(default_factory=SLODefaults)


class ResolvedPlan(BaseModel):
    graph_name: str
    service: str
    environment: str
    region: str
    provider: Provider
    production: bool
    nodes: list[ResolvedNode]
    edges: list[GraphEdge]
    order: list[str] = Field(description="Topological apply order of node ids.")

    def node(self, node_id: str) -> ResolvedNode:
        for n in self.nodes:
            if n.id == node_id:
                return n
        raise KeyError(f"no resolved node {node_id!r}")

    def by_type(self, node_type: NodeType) -> list[ResolvedNode]:
        return [n for n in self.nodes if n.type == node_type]
