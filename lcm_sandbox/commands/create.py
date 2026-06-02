"""`lcm-sandbox create` command: orchestrate Phases 0-3."""

from __future__ import annotations

from pathlib import Path

from lcm_sandbox.core.docker_builder import DEFAULT_IMAGE_TAG, prepare_image
from lcm_sandbox.core.preflight import run_preflight
from lcm_sandbox.core.sync import sync_worktree
from lcm_sandbox.core.worktree import prepare_worktree
from lcm_sandbox.models import Phase1Result, SandboxConfig
from lcm_sandbox.utils.logger import get_logger

logger = get_logger(__name__)


def create_sandbox(
    config: SandboxConfig,
    *,
    existing_worktree_path: Path | None = None,
    image_tag: str = DEFAULT_IMAGE_TAG,
    build_image_if_missing: bool = False,
    dockerfile: Path | None = None,
    docker_context: Path | None = None,
    skip_docker: bool = False,
) -> Phase1Result:
    """Run Phases 0 -> 3 and return a Phase1Result.

    Phase 1 (the delivery phase, not the lifecycle phase) does NOT launch the
    container. It stops after the image is verified. Phase 4 (lifecycle) is
    added in a later delivery phase.
    """
    # Phase 0
    run_preflight(config, skip_docker_checks=skip_docker)

    # Phase 1
    wt = prepare_worktree(config, existing_worktree_path=existing_worktree_path)

    # Phase 2
    baseline = sync_worktree(config, wt.worktree_path, wt.sandbox_id)

    # Phase 3 (optional skip for local dev when no Dockerfile yet)
    image_id: str | None = None
    if not skip_docker:
        image = prepare_image(
            tag=image_tag,
            build_if_missing=build_image_if_missing,
            dockerfile=dockerfile,
            context_path=docker_context,
        )
        image_id = image.image_id

    result = Phase1Result(
        sandbox_id=wt.sandbox_id,
        status="ready_for_docker_launch",
        worktree_path=wt.worktree_path,
        repo_path=config.repo_path,
        branch=config.branch_name,
        latest_commit=baseline.latest_commit,
        phase=3,
        next_step="docker_launch",
        image_id=image_id,
        baseline=baseline,
    )
    logger.info(
        "create_complete",
        extra={
            "sandbox_id": result.sandbox_id,
            "status": result.status,
            "phase": result.phase,
            "image_id": image_id,
        },
    )
    return result
