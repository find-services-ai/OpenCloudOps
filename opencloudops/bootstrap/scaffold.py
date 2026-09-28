"""Agent-agnostic scaffold files, and the write/conflict machinery every
adapter shares.

Ported from ``bootstrap/scaffold.py``. The original rendered Jinja2
templates and copied verbatim markdown skill files that lived alongside it
in the same repo; OpenCloudOps has no equivalent skill-markdown library to
copy (its "skills" are the Python engine itself), so this version's
agent-agnostic output is narrower: one ``AGENTS.md`` summarizing the
platform contract in a form any tool -- or a person -- can read without
IDE-specific config.
"""

from __future__ import annotations

from pathlib import Path

from opencloudops.bootstrap.adapters._shared import (
    boot_protocol,
    env_chain,
    env_table_rows,
    occ_command_table,
)
from opencloudops.spec import PlatformContract


def render_agents_md(platform: PlatformContract) -> str:
    return f"""# AI agent platform -- {platform.org}/{platform.business_unit}

> Scaffolded by `occ bootstrap`. Works with any AI coding assistant that can
> read files from this repository and run a shell command.

This repository's infrastructure is managed through
[OpenCloudOps](https://github.com/find-services-ai/OpenCloudOps): grounded in
an approved pattern catalog, guarded by an enforced security baseline, never
applying anything on its own.

## Environments

| env | short | region | risk | approvers |
|---|---|---|---|---|
{env_table_rows(platform)}

Promotion order: `{env_chain(platform)}` -- never skip an environment.

## Boot protocol

{boot_protocol(platform)}

## Commands

| Command | What it does |
|---|---|
{occ_command_table()}

## Configuration

See `platform/platform.yaml` for the naming template, mandatory tags, the
full security baseline, and the autonomy ladder. See `patterns/*.yaml` for
the approved infrastructure catalog.
"""


def collect_files(platform: PlatformContract) -> dict[str, str]:
    """Every agent-agnostic file a bootstrap run would write.

    Used by ``Bootstrap.dry_run()`` to return content without touching the
    filesystem, and by ``Bootstrap.run()`` before the chosen adapter's own
    files are layered on top.
    """
    return {"AGENTS.md": render_agents_md(platform)}


def write_files(files: dict[str, str], target_dir: Path, overwrite: bool = False) -> list[str]:
    """Write {relative_path: content} into target_dir.

    Raises FileExistsError on the first conflict when overwrite=False --
    matching the original's fail-fast behaviour rather than writing a
    partial scaffold and leaving the caller to notice.
    """
    written: list[str] = []
    for rel_path, content in files.items():
        dest = target_dir / rel_path
        if dest.exists() and not overwrite:
            raise FileExistsError(f"{rel_path} already exists in target. Pass overwrite=True to replace it.")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        written.append(rel_path)
    return written


def append_gitignore(target_dir: Path) -> None:
    """Add OpenCloudOps-specific gitignore entries if not already present."""
    addition = "\n# OpenCloudOps\nlogs/\nmemories/\nout/\n"
    gitignore = target_dir / ".gitignore"
    if gitignore.exists():
        existing = gitignore.read_text(encoding="utf-8")
        if "# OpenCloudOps" in existing:
            return
        gitignore.write_text(existing.rstrip() + "\n" + addition, encoding="utf-8")
    else:
        gitignore.write_text(addition.lstrip("\n"), encoding="utf-8")
