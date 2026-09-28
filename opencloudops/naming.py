"""Enterprise resource naming.

One template in the platform contract decides every resource name in every
emitted language. Emitters never build a name themselves -- they read
``ResolvedNode.name`` -- so a naming change is a one-line change to
``platform.yaml`` rather than a sweep through five IaC backends.

Provider name rules that the generic template cannot satisfy (S3's global
namespace, IAM's 64-character ceiling, Azure storage accounts' "lowercase
alphanumeric only, 24 chars") are handled by ``overrides`` plus the
sanitiser below, not by hand-editing output.
"""

from __future__ import annotations

import hashlib
import re

from opencloudops.spec import NodeType, PlatformContract

_ILLEGAL = re.compile(r"[^a-zA-Z0-9-]+")
_COLLAPSE = re.compile(r"-{2,}")


class NamingError(ValueError):
    """Raised when a name cannot be produced without breaking the contract."""


def _sanitise(raw: str, separator: str, lowercase: bool) -> str:
    out = _ILLEGAL.sub(separator, raw)
    out = _COLLAPSE.sub(separator, out).strip(separator)
    return out.lower() if lowercase else out


def _truncate(name: str, max_length: int, seed: str) -> str:
    """Shorten deterministically. A blind trim collides across services
    (``...-projmgmt-api`` and ``...-projmgmt-apiv2`` both cut to the same
    prefix), so the tail carries a hash of the full name instead."""
    if len(name) <= max_length:
        return name
    digest = hashlib.sha256(seed.encode()).hexdigest()[:6]
    keep = max_length - len(digest) - 1
    if keep < 1:
        raise NamingError(
            f"naming.max_length={max_length} is too small to hold even a hash suffix"
        )
    return f"{name[:keep].rstrip('-')}-{digest}"


def resource_name(
    platform: PlatformContract,
    *,
    environment: str,
    service: str,
    component: str,
    node_type: NodeType,
    max_length: int | None = None,
    globally_unique: bool = False,
) -> str:
    """Render the platform naming template for one resource.

    ``globally_unique`` appends a short deterministic suffix derived from the
    org and service -- the same inputs always produce the same bucket name,
    but two orgs never collide in a shared namespace.
    """
    env = platform.environment(environment)
    naming = platform.naming
    template = naming.overrides.get(node_type.value, naming.template)
    abbrev = naming.abbreviations.get(node_type.value, node_type.value.split(".")[-1][:3])

    tokens = {
        "org": platform.org,
        "bu": platform.business_unit,
        "env": env.short,
        "env_name": env.name,
        "service": service,
        "component": component,
        "abbrev": abbrev,
        "region": env.region,
    }
    try:
        raw = template.format(**tokens)
    except KeyError as e:
        raise NamingError(
            f"naming template {template!r} references unknown token {e.args[0]!r}; "
            f"available: {', '.join(sorted(tokens))}"
        ) from None

    name = _sanitise(raw, naming.separator, naming.lowercase)

    if globally_unique:
        digest = hashlib.sha256(f"{platform.org}/{platform.business_unit}/{service}".encode()).hexdigest()[:8]
        name = f"{name}-{digest}"

    limit = max_length or naming.max_length
    return _truncate(name, limit, seed=raw)


def logical_id(*parts: str) -> str:
    """CamelCase identifier for languages that need one (CloudFormation
    logical ids, CDK construct ids). Deterministic, and stable across runs."""
    words: list[str] = []
    for p in parts:
        words.extend(w for w in re.split(r"[^a-zA-Z0-9]+", p) if w)
    return "".join(w[:1].upper() + w[1:] for w in words) or "Resource"


def tf_identifier(*parts: str) -> str:
    """snake_case identifier legal as a Terraform/Pulumi resource label."""
    joined = "_".join(parts)
    ident = re.sub(r"[^a-zA-Z0-9_]+", "_", joined).strip("_").lower()
    ident = re.sub(r"_{2,}", "_", ident)
    if not ident or ident[0].isdigit():
        ident = f"r_{ident}"
    return ident
