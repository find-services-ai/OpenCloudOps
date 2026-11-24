"""Grounding: the pattern catalog.

An agent in this framework cannot invent infrastructure. It can only select
from a catalog of approved patterns, and if the catalog has nothing for the
node type it was asked to build, the run fails closed with an explicit
"no approved pattern" error rather than improvising a resource nobody has
reviewed.

That is the whole grounding contract:

* every ``GraphNode.type`` must resolve to exactly one ``Pattern``
* only ``status: approved`` patterns resolve implicitly; an experimental
  pattern must be named explicitly in the graph, and a deprecated one is
  refused outright
* the pattern -- not the agent, and not the model -- supplies the security
  ``controls`` the guard later checks, and the SLO defaults the SRE agent
  starts from
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from opencloudops.spec import NodeType, Pattern, PatternStatus, Provider

DEFAULT_CATALOG = Path("patterns")


class GroundingError(Exception):
    """Raised when a node cannot be grounded in an approved pattern."""


class PatternCatalog:
    """An in-memory index of the pattern files on disk."""

    def __init__(self, patterns: list[Pattern]) -> None:
        self._by_id: dict[str, Pattern] = {}
        for p in patterns:
            if p.id in self._by_id:
                raise GroundingError(f"duplicate pattern id {p.id!r} in catalog")
            self._by_id[p.id] = p

    # -- loading ---------------------------------------------------------

    @classmethod
    def from_dir(cls, directory: Path | str = DEFAULT_CATALOG) -> PatternCatalog:
        directory = Path(directory)
        if not directory.is_dir():
            raise GroundingError(
                f"pattern catalog {directory}/ does not exist -- there is nothing to ground against"
            )
        patterns: list[Pattern] = []
        for path in sorted(directory.rglob("*.yaml")) + sorted(directory.rglob("*.yml")):
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            if raw is None:
                continue
            items = raw if isinstance(raw, list) else [raw]
            for item in items:
                try:
                    patterns.append(Pattern.model_validate(item))
                except Exception as e:
                    raise GroundingError(f"{path}: invalid pattern -- {e}") from e
        if not patterns:
            raise GroundingError(f"pattern catalog {directory}/ is empty")
        return cls(patterns)

    # -- lookup ----------------------------------------------------------

    def __len__(self) -> int:
        return len(self._by_id)

    def all(self) -> list[Pattern]:
        return sorted(self._by_id.values(), key=lambda p: p.id)

    def get(self, pattern_id: str) -> Pattern:
        try:
            return self._by_id[pattern_id]
        except KeyError:
            close = sorted(p for p in self._by_id if p.split("-")[0] == pattern_id.split("-")[0])
            hint = f" Did you mean one of: {', '.join(close)}?" if close else ""
            raise GroundingError(f"no pattern {pattern_id!r} in the catalog.{hint}") from None

    def candidates(self, node_type: NodeType, provider: Provider) -> list[Pattern]:
        return [
            p
            for p in self.all()
            if p.node_type == node_type and p.provider == provider
        ]

    def resolve(
        self,
        node_type: NodeType,
        provider: Provider,
        *,
        explicit: str | None = None,
    ) -> Pattern:
        """Pick the pattern for a node. Fails closed, loudly, and with the
        list of what *would* have worked -- an agent that gets this error can
        report a real gap in the catalog instead of guessing."""
        if explicit is not None:
            pattern = self.get(explicit)
            if pattern.status is PatternStatus.deprecated:
                raise GroundingError(
                    f"pattern {explicit!r} is deprecated and may not be used. "
                    f"{pattern.notes or 'No replacement recorded -- ask the platform team.'}"
                )
            if pattern.node_type != node_type:
                raise GroundingError(
                    f"pattern {explicit!r} builds {pattern.node_type.value}, "
                    f"but the node declares type {node_type.value}"
                )
            if pattern.provider != provider:
                raise GroundingError(
                    f"pattern {explicit!r} targets provider {pattern.provider.value}, "
                    f"but this plan targets {provider.value}"
                )
            return pattern

        approved = [
            p for p in self.candidates(node_type, provider) if p.status is PatternStatus.approved
        ]
        if len(approved) == 1:
            return approved[0]
        if not approved:
            other = self.candidates(node_type, provider)
            if other:
                raise GroundingError(
                    f"no *approved* pattern for {node_type.value} on {provider.value}; "
                    f"{', '.join(p.id for p in other)} exist but are "
                    f"{', '.join(sorted({p.status.value for p in other}))}. "
                    "Name one explicitly with `pattern:` if that is deliberate."
                )
            raise GroundingError(
                f"no pattern for {node_type.value} on {provider.value}. "
                "The catalog is the only thing this framework is allowed to build, "
                "so add a reviewed pattern before asking an agent for this resource."
            )
        raise GroundingError(
            f"{len(approved)} approved patterns for {node_type.value} on {provider.value} "
            f"({', '.join(p.id for p in approved)}); the graph node must name one with `pattern:`"
        )

    # -- params ----------------------------------------------------------

    def merge_params(self, pattern: Pattern, supplied: dict[str, Any]) -> dict[str, Any]:
        """Apply pattern defaults, reject unknown or out-of-range params.

        Unknown params are an error rather than a pass-through: silently
        forwarding an unrecognised key is how an agent smuggles an unreviewed
        setting into generated IaC.
        """
        unknown = sorted(set(supplied) - set(pattern.params))
        if unknown:
            raise GroundingError(
                f"pattern {pattern.id!r} has no parameter(s) {', '.join(unknown)}; "
                f"accepted: {', '.join(sorted(pattern.params)) or '(none)'}"
            )

        merged: dict[str, Any] = {}
        for key, spec in pattern.params.items():
            if key in supplied:
                value = supplied[key]
            elif spec.default is not None:
                value = spec.default
            elif spec.required:
                raise GroundingError(
                    f"pattern {pattern.id!r} requires parameter {key!r} "
                    f"({spec.description or 'no description'})"
                )
            else:
                continue

            if spec.allowed is not None and value not in spec.allowed:
                raise GroundingError(
                    f"{pattern.id}.{key}={value!r} is not allowed; "
                    f"permitted values: {', '.join(map(str, spec.allowed))}"
                )
            merged[key] = value
        return merged
