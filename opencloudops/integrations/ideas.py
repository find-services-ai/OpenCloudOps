"""Discovery: derive a resource graph from an application directory.

The framework this replaces asked a person twenty questions and wrote a
scaffold from the answers. Most of those answers are already sitting in the
repository -- the Dockerfile says which port it listens on and whether it runs
as root, the requirements say whether it talks to Postgres or S3, the source
says whether it writes files. Reading them is both faster and more honest than
asking, because the code cannot misremember.

What discovery will *not* do is guess at the things that carry real
consequence: data classification, cost centre and owner have no signal in a
repository, so they come back as explicit placeholders that the guard's tagging
check will reject until a person fills them in. A discovered graph is a draft,
and it says so.

This is what makes the ``ideas/`` MCP applications in the sibling repository a
usable test suite: fifteen real, independently written services, none of them
built with this framework in mind.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from opencloudops.spec import (
    DataClassification,
    Exposure,
    GraphEdge,
    GraphMetadata,
    GraphNode,
    GraphSpecBody,
    NodeType,
    Relation,
    ResourceGraph,
)

PLACEHOLDER_OWNER = "REPLACE-owner"
PLACEHOLDER_COST_CENTER = "REPLACE-cost-centre"

#: Dependency or source markers -> the node they imply. Ordered most specific
#: first; the first match for a given node type wins.
_SIGNALS: list[tuple[str, NodeType, str]] = [
    (r"\bpsycopg|\bpostgres|\basyncpg|sqlalchemy|\bsqlite3?\b|\.db\b", NodeType.relational, "db"),
    (r"\bboto3\b|\bs3\b|minio|blob storage", NodeType.object_store, "artifacts"),
    (r"\bcelery\b|\bsqs\b|rabbitmq|\bkafka\b|redis.*queue", NodeType.queue, "jobs"),
    (r"secretsmanager|keyvault|\bvault\b|api[_-]?key|access[_-]?token", NodeType.secret, "credentials"),
]

_PORT_RE = re.compile(r"EXPOSE\s+(\d+)", re.IGNORECASE)
_ENV_PORT_RE = re.compile(r"ENV\s+\w*PORT\w*[=\s]+(\d+)", re.IGNORECASE)
# Match the path after the host, not the "//" that starts the authority --
# a lazy match from HEALTHCHECK otherwise captures "//localhost:8101/health".
_HEALTH_RE = re.compile(r"HEALTHCHECK.*?https?://[^/\s]+(/\S*)", re.IGNORECASE | re.DOTALL)
_NONROOT_RE = re.compile(r"^\s*USER\s+(?!root\b)\S+", re.IGNORECASE | re.MULTILINE)

#: Directories and files worth reading. Everything else is noise, and reading
#: it would be the "full workspace scan" the learner flags as a cost bug.
_READ_GLOBS = ("Dockerfile", "requirements*.txt", "package.json", "pyproject.toml", "src/*.py", "*.md")

_MAX_BYTES_PER_FILE = 200_000


class DiscoveryError(Exception):
    """Raised when a directory cannot be read as an application."""


class Discovery:
    """What was found, and what it implies. Kept separate from the graph so the
    evidence is reportable -- 'why is there a database node' has an answer."""

    def __init__(self, app_dir: Path) -> None:
        self.app_dir = app_dir
        self.name = app_dir.name
        self.evidence: dict[str, list[str]] = {}
        self.port = 8080
        self.health_path = "/health"
        self.runs_as_nonroot = False
        self.containerised = False
        self.headless_browser = False
        self.text = ""

    def note(self, key: str, detail: str) -> None:
        self.evidence.setdefault(key, []).append(detail)


def _read_sources(app_dir: Path) -> tuple[str, list[str]]:
    blobs: list[str] = []
    read: list[str] = []
    for pattern in _READ_GLOBS:
        for path in sorted(app_dir.glob(pattern)):
            if not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")[:_MAX_BYTES_PER_FILE]
            except OSError:
                continue
            blobs.append(content)
            read.append(path.name)
    return "\n".join(blobs), read


def _inspect(app_dir: Path) -> Discovery:
    if not app_dir.is_dir():
        raise DiscoveryError(f"{app_dir} is not a directory")
    d = Discovery(app_dir)
    text, read = _read_sources(app_dir)
    if not read:
        raise DiscoveryError(
            f"{app_dir} has nothing discovery can read "
            f"(looked for {', '.join(_READ_GLOBS)})"
        )
    d.text = text
    d.note("files_read", ", ".join(read))

    dockerfile = app_dir / "Dockerfile"
    if dockerfile.exists():
        docker_text = dockerfile.read_text(encoding="utf-8", errors="replace")
        d.containerised = True
        port_match = _PORT_RE.search(docker_text) or _ENV_PORT_RE.search(docker_text)
        if port_match:
            d.port = int(port_match.group(1))
            d.note("port", f"Dockerfile EXPOSE/ENV -> {d.port}")
        health = _HEALTH_RE.search(docker_text)
        if health:
            d.health_path = health.group(1)
            d.note("health", f"Dockerfile HEALTHCHECK -> {d.health_path}")
        if _NONROOT_RE.search(docker_text):
            d.runs_as_nonroot = True
            d.note("security", "Dockerfile drops to a non-root USER")
        else:
            d.note("security", "Dockerfile never leaves root -- the Kubernetes backend will enforce non-root anyway")

    if re.search(r"playwright|selenium|puppeteer|headless", text, re.IGNORECASE):
        d.headless_browser = True
        d.note("workload", "headless browser automation -- sized larger and given a longer timeout")
    return d


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "app"


def _service_token(name: str) -> str:
    """A 2-12 char [a-z0-9] token, because it goes inside every resource name.

    Built from the initials of multi-word names ('project-management' ->
    'projmgmt' would need a dictionary, so this takes a deterministic prefix of
    each word instead) and truncated rather than hashed, since a human reads it.
    """
    words = [w for w in re.split(r"[^a-z0-9]+", name.lower()) if w]
    if not words:
        return "app"
    if len(words) == 1:
        return words[0][:12]
    # Four characters per word stays readable ('project-management' ->
    # 'projmana'); three once there are four or more words, so the token does
    # not lose its last component to truncation.
    per = 4 if len(words) <= 3 else 3
    token = "".join(w[:per] for w in words)[:12]
    return token or words[0][:12]


def discover(
    app_dir: Path | str,
    *,
    environments: list[str] | None = None,
    owner: str = PLACEHOLDER_OWNER,
    cost_center: str = PLACEHOLDER_COST_CENTER,
    data_classification: DataClassification = DataClassification.confidential,
) -> tuple[ResourceGraph, Discovery]:
    """Derive a draft resource graph from an application directory.

    ``data_classification`` defaults to ``confidential`` deliberately. Guessing
    'internal' from a repository that happens not to mention personal data is
    how a service ends up under-protected; the safer default is the one a human
    has to actively relax.
    """
    app_dir = Path(app_dir)
    d = _inspect(app_dir)
    environments = environments or ["dev", "prod"]
    name = _slug(d.name)
    service = _service_token(d.name)

    nodes: list[GraphNode] = [
        GraphNode(
            id="network",
            type=NodeType.network,
            description="Three-tier network for this service.",
            params={"cidr": "10.40.0.0/16", "availability_zones": 2},
        )
    ]
    edges: list[GraphEdge] = []

    if d.containerised:
        nodes.append(
            GraphNode(
                id="app",
                type=NodeType.container_service,
                description=f"The {d.name} service.",
                exposure=Exposure.internal,
                params={
                    "cpu": 1024 if d.headless_browser else 512,
                    "memory_mb": 2048 if d.headless_browser else 1024,
                    "desired_count": 2,
                    "container_port": d.port,
                    "health_check_path": d.health_path,
                },
            )
        )
        edges.append(GraphEdge(**{"from": "app", "to": "network", "relation": Relation.depends_on}))
        d.note("compute", "Dockerfile present -> compute.container_service")
    else:
        nodes.append(
            GraphNode(
                id="app",
                type=NodeType.function,
                description=f"The {d.name} entrypoint.",
                params={
                    "runtime": "python3.12",
                    "memory_mb": 1024 if d.headless_browser else 512,
                    "timeout_seconds": 300 if d.headless_browser else 30,
                },
            )
        )
        edges.append(GraphEdge(**{"from": "app", "to": "network", "relation": Relation.depends_on}))
        d.note("compute", "no Dockerfile -> compute.function")

    seen: set[NodeType] = set()
    for pattern, node_type, node_id in _SIGNALS:
        if node_type in seen:
            continue
        match = re.search(pattern, d.text, re.IGNORECASE)
        if not match:
            continue
        seen.add(node_type)
        d.note("dependency", f"{match.group(0)!r} -> {node_type.value} node {node_id!r}")

        if node_type is NodeType.relational:
            nodes.append(
                GraphNode(
                    id=node_id,
                    type=node_type,
                    description="Primary store. SQLite in the repository becomes a managed database in the cloud.",
                    data_classification=data_classification,
                    params={"multi_az": True, "backup_retention_days": 14, "database_name": service},
                )
            )
            edges.append(GraphEdge(**{"from": node_id, "to": "network", "relation": Relation.depends_on}))
            edges.append(GraphEdge(**{"from": "app", "to": node_id, "relation": Relation.writes}))
        elif node_type is NodeType.object_store:
            nodes.append(
                GraphNode(
                    id=node_id,
                    type=node_type,
                    description="Generated documents and uploads.",
                    data_classification=data_classification,
                    params={"versioning": True, "lifecycle_expire_days": 365},
                )
            )
            edges.append(GraphEdge(**{"from": "app", "to": node_id, "relation": Relation.writes}))
        elif node_type is NodeType.queue:
            nodes.append(
                GraphNode(
                    id=node_id,
                    type=node_type,
                    description="Async work handed off by the service.",
                    params={"visibility_timeout_seconds": 60, "max_receive_count": 3},
                )
            )
            edges.append(GraphEdge(**{"from": "app", "to": node_id, "relation": Relation.publishes}))
        elif node_type is NodeType.secret:
            nodes.append(
                GraphNode(
                    id=node_id,
                    type=node_type,
                    description="Credentials for the external systems this service calls.",
                    params={"rotation_days": 30},
                )
            )
            edges.append(GraphEdge(**{"from": "app", "to": node_id, "relation": Relation.reads}))

    nodes.append(
        GraphNode(
            id="observability",
            type=NodeType.observability,
            description="Logs, dashboard and SLO alarms.",
            params={"log_retention_days": 30},
        )
    )
    edges.append(
        GraphEdge(**{"from": "observability", "to": "app", "relation": Relation.depends_on})
    )

    graph = ResourceGraph(
        metadata=GraphMetadata(
            name=name,
            service=service,
            owner=owner,
            cost_center=cost_center,
            data_classification=data_classification,
            environments=environments,
            source_idea=f"Discovered from {app_dir}",
        ),
        spec=GraphSpecBody(nodes=nodes, edges=edges),
    )
    return graph, d


def discover_all(
    ideas_dir: Path | str, **kwargs: Any
) -> dict[str, tuple[ResourceGraph, Discovery]]:
    """Discover every application directory under ``ideas_dir``.

    Directories discovery cannot read are skipped rather than raised: a mixed
    repository is the normal case, and failing the whole sweep because one
    folder holds documentation would make this unusable.
    """
    ideas_dir = Path(ideas_dir)
    if not ideas_dir.is_dir():
        raise DiscoveryError(f"{ideas_dir} is not a directory")
    out: dict[str, tuple[ResourceGraph, Discovery]] = {}
    for child in sorted(p for p in ideas_dir.iterdir() if p.is_dir()):
        if child.name.startswith((".", "_")):
            continue
        try:
            out[child.name] = discover(child, **kwargs)
        except DiscoveryError:
            continue
    return out


def to_yaml(graph: ResourceGraph, discovery: Discovery | None = None) -> str:
    """Serialise a discovered graph, with its evidence as a comment header.

    The evidence is the point: a reviewer should be able to see why a database
    node is there without re-running discovery.
    """
    source = discovery.app_dir if discovery else "an application directory"
    header = [f"# Discovered by OpenCloudOps from {source}."]
    if PLACEHOLDER_OWNER in (graph.metadata.owner, graph.metadata.cost_center) or (
        PLACEHOLDER_COST_CENTER in (graph.metadata.owner, graph.metadata.cost_center)
    ):
        # Only warn when the placeholders are still there. A header that cries
        # draft over a completed graph is a header people learn to ignore.
        header += [
            "#",
            "# DRAFT. Discovery reads a repository; it cannot know who owns this service or",
            "# which cost centre pays for it. Those fields are placeholders, and guard rule",
            "# GOV-006 blocks any plan whose tags still carry one -- so this draft cannot",
            "# reach a cloud account until a person fills them in.",
        ]
    else:
        header += [
            "#",
            "# Owner and cost centre were supplied by a person -- discovery cannot infer",
            "# them. Everything else below was read out of the application itself.",
        ]
    if discovery:
        header.append("#")
        header.append("# Evidence:")
        for key, notes in discovery.evidence.items():
            for note in notes:
                header.append(f"#   {key}: {note}")
    body = graph.model_dump(mode="json", by_alias=True, exclude_none=True)
    return "\n".join(header) + "\n" + yaml.safe_dump(body, sort_keys=False, width=100)
