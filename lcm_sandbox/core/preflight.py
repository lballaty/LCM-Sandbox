"""Phase 0: pre-flight validation.

Implements the eight checks specified in SANDBOX-DETAILED-FLOW.md STEP 0.1.
Each check raises `PreflightCheckError` with the check number and the
relevant context fields. The first failure aborts; we do not collect.
"""

from __future__ import annotations

import json

from lcm_sandbox.exceptions import PreflightCheckError
from lcm_sandbox.models import SandboxConfig
from lcm_sandbox.utils import docker as docker_utils
from lcm_sandbox.utils import git as git_utils
from lcm_sandbox.utils.logger import get_logger

logger = get_logger(__name__)


def run_preflight(config: SandboxConfig, *, skip_docker_checks: bool = False) -> None:
    """Run the preflight checks. Raises PreflightCheckError on the first failure.

    `skip_docker_checks=True` omits checks 3, 4, and 8 (colima, docker daemon,
    duplicate sandbox container). Used when the caller intends to skip Phase 3
    entirely (local dev without a docker host).
    """
    _check_repo(config)
    _check_branch(config)
    if not skip_docker_checks:
        _check_colima(config)
        _check_docker(config)
    _check_allowed_paths_json(config)
    _check_allowed_paths_structure(config)
    _check_timeout(config)
    if not skip_docker_checks:
        _check_no_duplicate_sandbox(config)
    logger.info(
        "preflight_passed",
        extra={
            "run_id": config.run_id,
            "phase": 0,
            "docker_checks_skipped": skip_docker_checks,
        },
    )


def _check_repo(config: SandboxConfig) -> None:
    if not config.repo_path.exists():
        raise PreflightCheckError(
            "repo_path does not exist", check=1, repo_path=str(config.repo_path)
        )
    result = git_utils.rev_parse_git_dir(config.repo_path)
    if not result.ok:
        raise PreflightCheckError(
            "repo_path is not a git repository",
            check=1,
            repo_path=str(config.repo_path),
            stderr=result.stderr.strip(),
        )


def _check_branch(config: SandboxConfig) -> None:
    local = git_utils.show_ref(f"refs/heads/{config.branch_name}", config.repo_path)
    if local.ok:
        return
    remote = git_utils.show_ref(
        f"refs/remotes/origin/{config.branch_name}", config.repo_path
    )
    if remote.ok:
        return
    raise PreflightCheckError(
        "branch not found locally or on origin",
        check=2,
        branch_name=config.branch_name,
        repo_path=str(config.repo_path),
    )


def _check_colima(config: SandboxConfig) -> None:
    result = docker_utils.colima_status(config.colima_profile)
    if not result.ok:
        raise PreflightCheckError(
            "colima profile not running",
            check=3,
            colima_profile=config.colima_profile,
            stderr=result.stderr.strip() or result.stdout.strip(),
        )
    combined = (result.stdout + result.stderr).lower()
    if "running" not in combined:
        raise PreflightCheckError(
            "colima profile is not in Running state",
            check=3,
            colima_profile=config.colima_profile,
            output=combined.strip(),
        )


def _check_docker(config: SandboxConfig) -> None:
    result = docker_utils.ps()
    if not result.ok:
        raise PreflightCheckError(
            "docker daemon not accessible",
            check=4,
            stderr=result.stderr.strip(),
        )


def _check_allowed_paths_json(config: SandboxConfig) -> None:
    try:
        json.loads(config.allowed_paths.model_dump_json())
    except json.JSONDecodeError as exc:
        raise PreflightCheckError(
            "allowed_paths is not valid JSON",
            check=5,
            error=str(exc),
        ) from exc


def _check_allowed_paths_structure(config: SandboxConfig) -> None:
    if not isinstance(config.allowed_paths.write, list) or not isinstance(
        config.allowed_paths.read, list
    ):
        raise PreflightCheckError(
            "allowed_paths must have list-valued 'write' and 'read' keys",
            check=6,
        )


def _check_timeout(config: SandboxConfig) -> None:
    if not (15 <= config.timeout_minutes <= 480):
        raise PreflightCheckError(
            "timeout_minutes must be between 15 and 480",
            check=7,
            timeout_minutes=config.timeout_minutes,
        )


def _check_no_duplicate_sandbox(config: SandboxConfig) -> None:
    result = docker_utils.ps_filter(f"run_id={config.run_id}")
    if not result.ok:
        raise PreflightCheckError(
            "docker ps failed during duplicate-sandbox check",
            check=8,
            stderr=result.stderr.strip(),
        )
    if result.stdout.strip():
        raise PreflightCheckError(
            "another sandbox container is already running for this run_id",
            check=8,
            run_id=config.run_id,
            container_ids=result.stdout.strip().splitlines(),
        )
