"""`lcm-sandbox` CLI entry point."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import click

from lcm_sandbox import __version__
from lcm_sandbox.commands.create import create_sandbox
from lcm_sandbox.core.artifact_capture import cleanup_sandbox
from lcm_sandbox.core.docker_launcher import (
    DEFAULT_IMAGE_TAG,
    container_status,
    launch_container,
    parse_egress_allowlist,
    stop_container,
)
from lcm_sandbox.exceptions import (
    DockerImageError,
    DockerLaunchError,
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
    DockerLaunchError: 5,
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


@main.command("launch")
@click.option("--sandbox-id", required=True,
              help="Sandbox / run id. Used as the container name.")
@click.option("--worktree-path", required=True,
              type=click.Path(exists=True, file_okay=False),
              help="Absolute host path to the prepared worktree (Phase 1 output).")
@click.option("--plan-id", default=None,
              help="Plan id. Defaults to sandbox-id when omitted.")
@click.option("--branch", "branch_name", default="sandbox",
              help="Branch label written into the manifest.")
@click.option("--allowed-paths", "allowed_paths_json",
              default='{"write":[],"read":["*"]}', show_default=True,
              help='JSON object: {"write":["src/"],"read":["*"]}')
@click.option("--timeout", "timeout_minutes", default=60, show_default=True, type=int)
@click.option("--hermes-persona", default=None,
              help="Persona key. If set, entrypoint renders state + starts hermes gateway.")
@click.option("--image-tag", default=DEFAULT_IMAGE_TAG, show_default=True)
@click.option("--mcp-url", default=None)
@click.option("--mcp-token", default=None)
@click.option("--model-provider", default=None)
@click.option("--model-key", default=None)
@click.option("--egress-allowlist", "egress_allowlist_spec", default=None,
              help='Comma-separated HOST[:PORT] entries the sandbox is allowed '
                   'to reach. Advisory until the host-side restricted bridge '
                   'is wired up; the value is forwarded as LCM_EGRESS_ALLOWLIST '
                   'env var so the entrypoint can install rules where supported.')
def launch(
    sandbox_id: str,
    worktree_path: str,
    plan_id: str | None,
    branch_name: str,
    allowed_paths_json: str,
    timeout_minutes: int,
    hermes_persona: str | None,
    image_tag: str,
    mcp_url: str | None,
    mcp_token: str | None,
    model_provider: str | None,
    model_key: str | None,
    egress_allowlist_spec: str | None,
) -> None:
    """Launch a sandbox container (Phase 4)."""
    try:
        parsed_paths = json.loads(allowed_paths_json)
    except json.JSONDecodeError as exc:
        _die(DockerLaunchError(
            "--allowed-paths is not valid JSON", step="4.1", error=str(exc),
        ))

    try:
        config = SandboxConfig(
            plan_id=plan_id or sandbox_id,
            run_id=sandbox_id,
            repo_path=Path(worktree_path).resolve(),
            branch_name=branch_name,
            allowed_paths=AllowedPaths(**parsed_paths),
            timeout_minutes=timeout_minutes,
        )
    except Exception as exc:
        _die(DockerLaunchError(f"invalid launch configuration: {exc}", step="4.1"))

    try:
        egress_allowlist = parse_egress_allowlist(egress_allowlist_spec)
    except ValueError as exc:
        _die(DockerLaunchError(str(exc), step="4.1", spec=egress_allowlist_spec))

    try:
        result = launch_container(
            config,
            image_tag=image_tag,
            worktree_path=Path(worktree_path).resolve(),
            hermes_persona=hermes_persona,
            mcp_url=mcp_url,
            mcp_token=mcp_token,
            model_provider=model_provider,
            model_key=model_key,
            egress_allowlist=egress_allowlist or None,
        )
    except SandboxError as exc:
        _die(exc)

    click.echo(result.model_dump_json(indent=2))
    sys.exit(0 if result.status == "running" else 5)


@main.command("stop")
@click.option("--sandbox-id", required=True)
@click.option("--keep/--remove", default=False,
              help="Keep the stopped container for post-mortem (default: remove).")
def stop(sandbox_id: str, keep: bool) -> None:
    """Stop and (by default) remove a sandbox container.

    WP-3 will run artifact capture before invoking this; for now it's a teardown stub.
    """
    payload = stop_container(sandbox_id, remove=not keep)
    click.echo(json.dumps(payload, indent=2))
    sys.exit(0 if payload.get("stopped") else 5)


@main.command("status")
@click.option("--sandbox-id", required=True)
def status(sandbox_id: str) -> None:
    """Print the current status of a sandbox container as JSON."""
    payload = container_status(sandbox_id)
    click.echo(json.dumps(payload, indent=2))
    sys.exit(0)


@main.command("cleanup")
@click.option("--sandbox-id", required=True,
              help="Sandbox / run id (same value passed to `launch`).")
@click.option("--worktree-path", required=True,
              type=click.Path(file_okay=False),
              help="Host worktree path produced by Phase 1.")
@click.option("--branch", "branch_name", default="sandbox", show_default=True)
@click.option("--allowed-paths", "allowed_paths_json",
              default='{"write":[],"read":["*"]}', show_default=True,
              help='JSON object the run used; only the shape matters here.')
@click.option("--plan-id", default=None,
              help="Plan id. Defaults to sandbox-id when omitted.")
@click.option("--keep-artifacts/--remove-artifacts", default=True, show_default=True,
              help="Whether to keep the archived artifacts directory under "
                   "~/.lcm-sandbox/artifacts/<sandbox-id>/.")
@click.option("--keep-worktree/--remove-worktree", default=False, show_default=True,
              help="Whether to leave the host worktree on disk.")
def cleanup(
    sandbox_id: str,
    worktree_path: str,
    branch_name: str,
    allowed_paths_json: str,
    plan_id: str | None,
    keep_artifacts: bool,
    keep_worktree: bool,
) -> None:
    """Stop + remove the container and (by default) the worktree.

    Idempotent: re-running after a successful cleanup returns
    "already-absent" / "none" markers instead of failing. Artifacts captured
    by a prior `capture` invocation are preserved unless --remove-artifacts.
    """
    try:
        parsed = json.loads(allowed_paths_json)
    except json.JSONDecodeError as exc:
        _die(SandboxError(
            "--allowed-paths is not valid JSON",
            phase=6, step="6.6", error=str(exc),
        ))

    try:
        config = SandboxConfig(
            plan_id=plan_id or sandbox_id,
            run_id=sandbox_id,
            repo_path=Path(worktree_path).resolve(),
            branch_name=branch_name,
            allowed_paths=AllowedPaths(**parsed),
            timeout_minutes=15,
        )
    except Exception as exc:
        _die(SandboxError(f"invalid cleanup configuration: {exc}", phase=6, step="6.6"))

    payload = cleanup_sandbox(
        config,
        keep_artifacts=keep_artifacts,
        remove_worktree=not keep_worktree,
    )
    click.echo(json.dumps(payload, indent=2))
    sys.exit(0)


@main.command("scaffold")
@click.option("--plan", "plan_path", required=True,
              type=click.Path(dir_okay=False, exists=True),
              help="Path to plan.json containing scaffolding_actions[].")
@click.option("--target-path", "target_path", required=True,
              type=click.Path(file_okay=False),
              help="Absolute directory to scaffold into (must NOT exist).")
@click.option("--control-dir", "control_dir", required=True,
              type=click.Path(file_okay=False),
              help="Control directory (status.json + events.jsonl written here).")
@click.option("--run-id", "run_id_opt", default=None,
              help="Run identifier (auto-generated if omitted).")
def scaffold(plan_path: str, target_path: str, control_dir: str,
             run_id_opt: str | None) -> None:
    """Execute a deterministic scaffolding plan (RCW-4 Slice B v0).

    Reads plan.scaffolding_actions[] and runs each action inline on the host,
    emitting per-action events via the standard control-plane modules so
    AIDevOps polling sees the same shape as Hermes runs.
    """
    from lcm_sandbox.commands.scaffold import (
        ScaffoldError,
        execute_scaffold_plan,
        load_plan,
    )
    from lcm_sandbox.core.control_plane import ControlPaths

    try:
        plan = load_plan(Path(plan_path))
        resolved_run_id = (run_id_opt or plan.get("run_id")
                           or f"scaffold-{uuid.uuid4().hex[:12]}")
        ctrl = ControlPaths(root=Path(control_dir))
        ctrl.ensure_layout()
        result = execute_scaffold_plan(
            plan,
            Path(target_path),
            ctrl,
            run_id=resolved_run_id,
        )
        payload = {
            "status": "ok" if result.success else "failed",
            "run_id": resolved_run_id,
            "target_path": str(result.target_path),
            "actions": result.actions,
            "failed_action_index": result.failed_action_index,
            "error_message": result.error_message,
        }
        click.echo(json.dumps(payload, indent=2))
        sys.exit(0 if result.success else 6)
    except ScaffoldError as exc:
        click.echo(json.dumps(
            {"status": "error", "error_type": "ScaffoldError", "message": str(exc)},
            indent=2,
        ), err=True)
        sys.exit(6)


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
