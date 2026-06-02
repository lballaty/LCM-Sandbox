"""Thin wrappers over `docker` and `colima` subprocess calls."""

from __future__ import annotations

from pathlib import Path

from lcm_sandbox.utils.shell import CommandResult, run


def _docker(args: list[str]) -> CommandResult:
    return run(["docker", *args])


def ps() -> CommandResult:
    return _docker(["ps"])


def ps_filter(label: str) -> CommandResult:
    return _docker(["ps", "--filter", f"label={label}", "--format", "{{.ID}}"])


def image_inspect(image: str) -> CommandResult:
    return _docker(["image", "inspect", image])


def image_id(image: str) -> str | None:
    result = _docker(["image", "inspect", "--format", "{{.Id}}", image])
    if result.ok:
        return result.stdout.strip() or None
    return None


def image_build(tag: str, context_path: Path, dockerfile: Path | None = None) -> CommandResult:
    args = ["build", "-t", tag]
    if dockerfile:
        args.extend(["-f", str(dockerfile)])
    args.append(str(context_path))
    return _docker(args)


def run_check(image: str, command: list[str]) -> CommandResult:
    return _docker(["run", "--rm", image, *command])


def colima_status(profile: str) -> CommandResult:
    return run(["colima", "-p", profile, "status"])
