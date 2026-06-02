"""`lcm-sandbox` CLI entry point."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import click

from lcm_sandbox import __version__
from lcm_sandbox.commands.create import create_sandbox
from lcm_sandbox.exceptions import (
    DockerImageError,
    PreflightCheckError,
    SandboxError,
    SyncError,
    WorktreeError,
)
from lcm_sandbox.models import AllowedPaths, SandboxConfig
from lcm_sandbox.utils.logger import configure as configure_logger

# Exit code mapping. 0 = success; 1-4 mirror the phase that failed.
_EXIT_CODES = {
    PreflightCheckError: 1,
    WorktreeError: 2,
    SyncError: 3,
    DockerImageError: 4,
}


@click.group()
@click.version_option(__version__, prog_name="lcm-sandbox")
@click.option("--verbose", is_flag=True, help="Enable debug logging.")
def main(verbose: bool) -> None:
    """LCM-Sandbox: isolated worktree + Docker sandbox for agent execution."""
    import logging
    configure_logger(level=logging.DEBUG if verbose else logging.INFO)


@main.command("create")
@click.option("--repo", "repo_path", required=True, type=click.Path(file_okay=False),
              help="Absolute path to the host git repository.")
@click.option("--branch", "branch_name", required=True,
              help="Branch name to use inside the worktree.")
@click.option("--allowed-paths", "allowed_paths_json", required=True,
              help='JSON object: {"write":["src/"],"read":["*"]}')
@click.option("--timeout", "timeout_minutes", default=60, show_default=True, type=int,
              help="Sandbox timeout in minutes (15-480).")
@click.option("--colima-profile", default="LCM-Dev", show_default=True,
              help="Colima profile to validate.")
@click.option("--plan-id", default=None,
              help="Plan identifier (auto-generated if omitted).")
@click.option("--run-id", default=None,
              help="Run identifier (auto-generated if omitted).")
@click.option("--existing-worktree", "existing_worktree", default=None,
              type=click.Path(file_okay=False, exists=True),
              help="Reuse an existing worktree (multi-step plan).")
@click.option("--image-tag", default="lcm-dev-agent:latest", show_default=True,
              help="Docker image tag to verify.")
@click.option("--build-image/--no-build-image", default=False,
              help="Build the image if it is not present locally.")
@click.option("--dockerfile", default=None, type=click.Path(dir_okay=False),
              help="Dockerfile path (required if --build-image and image absent).")
@click.option("--docker-context", default=None, type=click.Path(file_okay=False),
              help="Docker build context (required if --build-image and image absent).")
@click.option("--skip-docker", is_flag=True,
              help="Skip Phase 3 entirely (local dev when image/Dockerfile absent).")
def create(
    repo_path: str,
    branch_name: str,
    allowed_paths_json: str,
    timeout_minutes: int,
    colima_profile: str,
    plan_id: str | None,
    run_id: str | None,
    existing_worktree: str | None,
    image_tag: str,
    build_image: bool,
    dockerfile: str | None,
    docker_context: str | None,
    skip_docker: bool,
) -> None:
    """Create (or reuse) a sandbox worktree and prepare it for launch."""
    try:
        parsed_paths = json.loads(allowed_paths_json)
    except json.JSONDecodeError as exc:
        _die(PreflightCheckError(
            "--allowed-paths is not valid JSON",
            check=5,
            error=str(exc),
            input=allowed_paths_json,
        ))

    try:
        config = SandboxConfig(
            plan_id=plan_id or f"plan_{uuid.uuid4().hex[:12]}",
            run_id=run_id or f"run_{uuid.uuid4().hex[:12]}",
            repo_path=Path(repo_path).resolve(),
            branch_name=branch_name,
            allowed_paths=AllowedPaths(**parsed_paths),
            timeout_minutes=timeout_minutes,
            colima_profile=colima_profile,
        )
    except Exception as exc:  # pydantic ValidationError, ValueError, etc.
        _die(PreflightCheckError(
            f"invalid sandbox configuration: {exc}",
            check=0,
        ))

    try:
        result = create_sandbox(
            config,
            existing_worktree_path=Path(existing_worktree) if existing_worktree else None,
            image_tag=image_tag,
            build_image_if_missing=build_image,
            dockerfile=Path(dockerfile) if dockerfile else None,
            docker_context=Path(docker_context) if docker_context else None,
            skip_docker=skip_docker,
        )
    except SandboxError as exc:
        _die(exc)

    click.echo(result.model_dump_json(indent=2))
    sys.exit(0)


def _die(exc: SandboxError) -> "None":
    payload = {
        "status": "error",
        "phase": exc.phase,
        "step": exc.step,
        "error_type": type(exc).__name__,
        "message": exc.message,
        "context": exc.context,
    }
    click.echo(json.dumps(payload, indent=2, default=str), err=True)
    sys.exit(_EXIT_CODES.get(type(exc), 10))


if __name__ == "__main__":
    main()
