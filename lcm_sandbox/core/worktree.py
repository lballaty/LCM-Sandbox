"""Phase 1: worktree preparation.

Implements STEP 1.1 (create new worktree) and STEP 1.2 (reset existing worktree).
The sandbox_id is built once here and reused throughout the rest of the run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from lcm_sandbox.exceptions import WorktreeError
from lcm_sandbox.models import SandboxConfig
from lcm_sandbox.utils import git as git_utils
from lcm_sandbox.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class WorktreeInfo:
    sandbox_id: str
    worktree_path: Path
    reused: bool


def make_sandbox_id(run_id: str, now: datetime | None = None) -> str:
    ts = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    return f"sandbox-{run_id}-{ts}"


def worktree_path_for(repo_path: Path, sandbox_id: str) -> Path:
    return repo_path / ".sandbox-worktrees" / sandbox_id


def prepare_worktree(
    config: SandboxConfig,
    existing_worktree_path: Path | None = None,
) -> WorktreeInfo:
    """Create a new worktree or reset an existing one.

    If `existing_worktree_path` is supplied (multi-step plan reuse), the
    worktree is reset to a clean state. Otherwise a fresh worktree is
    created under `<repo>/.sandbox-worktrees/<sandbox_id>/`.
    """
    if existing_worktree_path is not None:
        return _reset_existing(config, existing_worktree_path)
    return _create_new(config)


def _create_new(config: SandboxConfig) -> WorktreeInfo:
    sandbox_id = make_sandbox_id(config.run_id)
    worktree_path = worktree_path_for(config.repo_path, sandbox_id)
    worktree_parent = worktree_path.parent
    worktree_parent.mkdir(parents=True, exist_ok=True)

    # STEP 1.1.1: verify origin/main exists.
    ls = git_utils.ls_remote("origin", "main", config.repo_path)
    if not ls.ok or "refs/heads/main" not in ls.stdout:
        raise WorktreeError(
            "origin/main not found; cannot create worktree",
            step="1.1.1",
            repo_path=str(config.repo_path),
            stderr=ls.stderr.strip(),
        )

    # STEP 1.1.2: ensure local branch exists (track remote or branch from main).
    _ensure_local_branch(config)

    # STEP 1.1.3: create the worktree.
    add = git_utils.worktree_add(worktree_path, config.branch_name, config.repo_path)
    if not add.ok:
        raise WorktreeError(
            "git worktree add failed",
            step="1.1.3",
            worktree_path=str(worktree_path),
            branch_name=config.branch_name,
            stderr=add.stderr.strip(),
        )

    # Verify: the worktree's .git file (not directory) exists and HEAD resolves.
    if not (worktree_path / ".git").exists():
        raise WorktreeError(
            "worktree created but .git file missing",
            step="1.1.3",
            worktree_path=str(worktree_path),
        )
    head = git_utils.rev_parse("HEAD", worktree_path)
    if not head.ok or not head.stdout.strip():
        raise WorktreeError(
            "worktree created but HEAD does not resolve",
            step="1.1.3",
            worktree_path=str(worktree_path),
            stderr=head.stderr.strip(),
        )

    logger.info(
        "worktree_created",
        extra={
            "phase": 1,
            "step": "1.1",
            "sandbox_id": sandbox_id,
            "worktree_path": str(worktree_path),
            "branch": config.branch_name,
            "head": head.stdout.strip(),
        },
    )
    return WorktreeInfo(sandbox_id=sandbox_id, worktree_path=worktree_path, reused=False)


def _ensure_local_branch(config: SandboxConfig) -> None:
    local = git_utils.show_ref(f"refs/heads/{config.branch_name}", config.repo_path)
    if local.ok:
        return
    remote = git_utils.ls_remote("origin", config.branch_name, config.repo_path)
    if remote.ok and f"refs/heads/{config.branch_name}" in remote.stdout:
        track = git_utils.branch_track(
            config.branch_name, f"origin/{config.branch_name}", config.repo_path
        )
        if not track.ok:
            raise WorktreeError(
                "failed to create tracking branch",
                step="1.1.2",
                branch_name=config.branch_name,
                stderr=track.stderr.strip(),
            )
        return
    # Neither local nor remote: branch from origin/main.
    create = git_utils.checkout(
        config.branch_name, config.repo_path, create_from="origin/main"
    )
    if not create.ok:
        raise WorktreeError(
            "failed to create branch from origin/main",
            step="1.1.2",
            branch_name=config.branch_name,
            stderr=create.stderr.strip(),
        )


def _reset_existing(config: SandboxConfig, worktree_path: Path) -> WorktreeInfo:
    # STEP 1.2.1: verify worktree is still valid.
    git_dir = git_utils.rev_parse_git_dir(worktree_path)
    if not git_dir.ok:
        raise WorktreeError(
            "existing worktree is corrupted (rev-parse --git-dir failed)",
            step="1.2.1",
            worktree_path=str(worktree_path),
            stderr=git_dir.stderr.strip(),
        )

    # STEP 1.2.2: switch to correct branch.
    co = git_utils.checkout(config.branch_name, worktree_path)
    if not co.ok:
        raise WorktreeError(
            "failed to checkout branch in existing worktree",
            step="1.2.2",
            worktree_path=str(worktree_path),
            branch_name=config.branch_name,
            stderr=co.stderr.strip(),
        )

    # STEP 1.2.3: clean all changes and untracked files.
    reset = git_utils.reset_hard("HEAD", worktree_path)
    if not reset.ok:
        raise WorktreeError(
            "git reset --hard failed",
            step="1.2.3",
            worktree_path=str(worktree_path),
            stderr=reset.stderr.strip(),
        )
    clean = git_utils.clean_fd(worktree_path)
    if not clean.ok:
        raise WorktreeError(
            "git clean -fd failed",
            step="1.2.3",
            worktree_path=str(worktree_path),
            stderr=clean.stderr.strip(),
        )
    if not git_utils.is_clean(worktree_path):
        raise WorktreeError(
            "worktree still has changes after reset/clean",
            step="1.2.3",
            worktree_path=str(worktree_path),
        )

    # STEP 1.2.4: update upstream.
    upstream = git_utils.branch_set_upstream(
        config.branch_name, f"origin/{config.branch_name}", worktree_path
    )
    if not upstream.ok:
        # Non-fatal if remote tracking branch doesn't exist; just log.
        logger.warning(
            "branch_upstream_update_failed",
            extra={
                "step": "1.2.4",
                "worktree_path": str(worktree_path),
                "stderr": upstream.stderr.strip(),
            },
        )

    # Recover the original sandbox_id from the worktree directory name.
    sandbox_id = worktree_path.name
    logger.info(
        "worktree_reset",
        extra={
            "phase": 1,
            "step": "1.2",
            "sandbox_id": sandbox_id,
            "worktree_path": str(worktree_path),
        },
    )
    return WorktreeInfo(sandbox_id=sandbox_id, worktree_path=worktree_path, reused=True)
