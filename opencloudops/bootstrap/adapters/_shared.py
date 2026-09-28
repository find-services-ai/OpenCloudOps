"""Rendering helpers shared by every adapter.

Kept in one place so the environment table, the risk-tier explanation, and
the `occ` command reference read identically no matter which tool's file
format wraps them -- a platform contract change should produce the same
facts everywhere, not drift because one adapter's copy of the table fell
out of sync with another's.
"""

from __future__ import annotations

from opencloudops.spec import PlatformContract


def env_table_rows(platform: PlatformContract) -> str:
    return "\n".join(
        f"| {e.name} | `{e.short}` | `{e.region}` | "
        f"{'HIGH (production)' if e.production else 'LOW/MEDIUM'} | "
        f"{', '.join(e.approvers) or 'none declared'} |"
        for e in platform.environments
    )


def env_chain(platform: PlatformContract) -> str:
    return " -> ".join(e.name for e in platform.environments)


OCC_COMMANDS = [
    ("occ plan <graph> --env <env>", "Ground + guard. Emits and applies nothing."),
    ("occ iac <graph> --env <env> --language <lang>", "Emit IaC. Add --out DIR --approve iac-builder to write files."),
    ("occ guard <graph> --env <env>", "Security baseline only, no code emitted."),
    ("occ slo <graph> --env <env> --runbook FILE", "SLOs, burn-rate alerts, the on-call runbook."),
    ("occ diagnose <service> --signal \"<text>\" --env <env> --remediate", "Classify a signal, propose a fix."),
    ("occ list patterns|lessons|graphs|nodes", "What this installation is allowed to build and checks for."),
]


def occ_command_table() -> str:
    return "\n".join(f"| `{cmd}` | {desc} |" for cmd, desc in OCC_COMMANDS)


def boot_protocol(platform: PlatformContract) -> str:
    """The one paragraph every adapter repeats: what an agent must do before
    treating an OpenCloudOps result as final. Kept short and identical
    everywhere on purpose -- this is the one thing that must never drift."""
    return (
        f"OpenCloudOps has no apply path and holds no cloud credentials. "
        f"`occ plan`/`occ iac` only ever ground, guard, and (optionally) emit code -- "
        f"never say something was deployed or applied. Production "
        f"({', '.join(e.name for e in platform.environments if e.production) or 'none declared'}) "
        f"is excluded from autonomous remediation unconditionally, regardless of a "
        f"finding's confidence or risk tier. If `occ` reports \"no pattern for ... on ...\", "
        f"that is the framework correctly refusing to invent infrastructure -- report the gap, "
        f"never hand-write the equivalent to route around it."
    )
