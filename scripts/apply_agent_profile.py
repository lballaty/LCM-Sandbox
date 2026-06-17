#!/usr/bin/env python3
# File: LCM-Sandbox/scripts/apply_agent_profile.py
# Description: Renders the requested in-sandbox Claude Code agent profile
#              (permissive | standard) into <target>/.claude/settings.json
#              inside the lcm-hermes-agent container. Required by
#              SANDBOX-AGENT-CONFIG.md §"How the entrypoint applies the profile".
# Author: Libor Ballaty <libor@arionetworks.com>
# Created: 2026-06-16
#
# Usage:
#   apply_agent_profile.py --profile permissive --target /home/aiagent
#
# Behavior:
#   - Reads JSON template from <repo>/lcm_sandbox/templates/agent_profiles/<profile>.json
#     (resolved by walking up from this script's location, OR honoring
#     LCM_PROFILE_TEMPLATE_DIR if set so the entrypoint can point at the
#     image-baked copy).
#   - Writes the merged content to <target>/.claude/settings.json, creating
#     the directory if absent. Ownership is set to UID 1000 (aiagent) when
#     running as root.
#   - DOES NOT merge with any existing file: the canonical sandbox profile
#     must be the only source of truth inside the container. If the target
#     already exists it is overwritten.

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

VALID_PROFILES = ("permissive", "standard")


def resolve_template_dir() -> Path:
    """Locate the agent_profiles directory.

    Resolution order:
      1. $LCM_PROFILE_TEMPLATE_DIR (used inside the container — entrypoint
         exports this to the image-baked path).
      2. <repo>/lcm_sandbox/templates/agent_profiles (used in tests + dev).
    """
    override = os.environ.get("LCM_PROFILE_TEMPLATE_DIR")
    if override:
        return Path(override)
    here = Path(__file__).resolve()
    repo_root = here.parents[1]
    return repo_root / "lcm_sandbox" / "templates" / "agent_profiles"


def load_profile(profile: str, template_dir: Path) -> dict:
    if profile not in VALID_PROFILES:
        raise SystemExit(
            f"unknown profile {profile!r}; expected one of {VALID_PROFILES}"
        )
    path = template_dir / f"{profile}.json"
    if not path.exists():
        raise SystemExit(f"template not found: {path}")
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise SystemExit(f"template {path} is not valid JSON: {e}") from e


def apply(profile: str, target_home: Path, template_dir: Path) -> Path:
    config = load_profile(profile, template_dir)

    claude_dir = target_home / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    settings = claude_dir / "settings.json"
    settings.write_text(json.dumps(config, indent=2) + "\n")

    # Best-effort: chown to aiagent (UID 1000) when we have permission.
    # Inside the container the entrypoint runs as root so this succeeds; on
    # the host (tests) it's a no-op silently.
    try:
        os.chown(claude_dir, 1000, 1000)
        os.chown(settings, 1000, 1000)
    except (PermissionError, OSError):
        pass

    return settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render in-sandbox agent profile into target home dir.",
    )
    parser.add_argument(
        "--profile",
        required=True,
        choices=VALID_PROFILES,
        help="Profile name: permissive (default in container) or standard.",
    )
    parser.add_argument(
        "--target",
        required=True,
        type=Path,
        help="Target home directory (e.g. /home/aiagent).",
    )
    parser.add_argument(
        "--template-dir",
        type=Path,
        default=None,
        help="Override template dir; otherwise resolved from env or repo layout.",
    )
    args = parser.parse_args(argv)

    template_dir = args.template_dir or resolve_template_dir()
    written = apply(args.profile, args.target, template_dir)
    print(f"[apply_agent_profile] wrote {written}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
