"""`python3 -m bootstrap` -- scaffold a CloudOps agentic-AI repository.

    python3 -m bootstrap --target ../my-cloudops-repo
    python3 -m bootstrap --profile minimal --agent copilot --dry-run
    python3 -m bootstrap --config ./platform.yml --target . --overwrite

Flag-driven only: an interactive wizard is pleasant once and an obstacle in
CI, and this is meant to run in both.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bootstrap.config import AGENTS, PROFILES, ConfigError, PlatformConfig
from bootstrap.scaffold import conflicts, plan, write


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bootstrap", description=__doc__.split("\n")[0])
    p.add_argument("--target", default=".", help="repository root to scaffold into (default: .)")
    p.add_argument("--profile", choices=sorted(PROFILES), default="playground",
                   help="built-in configuration to start from (default: playground)")
    p.add_argument("--config", help="path to a platform.yml; overrides --profile")
    p.add_argument("--agent", choices=sorted(AGENTS), help="coding agent to emit entrypoint files for")
    p.add_argument("--dry-run", action="store_true", help="list files and byte counts, write nothing")
    p.add_argument("--overwrite", action="store_true", help="replace files that already exist")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        config = PlatformConfig.from_yaml(args.config) if args.config else PROFILES[args.profile]()
        if args.agent:
            config.agent = args.agent
            config.__post_init__()  # re-validate: --agent can make a loaded config invalid
    except (ConfigError, FileNotFoundError) as e:
        print(f"configuration error: {e}", file=sys.stderr)
        return 2

    target = Path(args.target)
    files = plan(config)

    if args.dry_run:
        print(f"{config.name} -> {target} ({len(files)} files, nothing written)")
        for rel, content in sorted(files.items()):
            marker = "OVERWRITES" if (target / rel).exists() else "new"
            print(f"  [{marker:>10}] {rel}  ({len(content)} bytes)")
        return 0

    existing = conflicts(config, target)
    if existing and not args.overwrite:
        print(f"refusing to overwrite {len(existing)} existing file(s):", file=sys.stderr)
        for rel in existing:
            print(f"  {rel}", file=sys.stderr)
        print("re-run with --overwrite if that is what you want.", file=sys.stderr)
        return 1

    for rel in write(config, target, overwrite=args.overwrite):
        print(f"wrote {target / rel}")
    print(f"\n{config.name}: {len(files)} files. Promotion path: {' -> '.join(config.promotion_path)}")
    if config.mesh:
        print("Next: point the sandbox-research server at mesh.yaml via SANDBOX_RESEARCH_MESH.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
