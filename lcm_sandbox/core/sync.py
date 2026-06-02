"""Phase 2: sync worktree to latest origin/main.

Implements STEP 2.1-2.6: fetch in parent repo, determine sync status, rebase
if behind, abort if ahead/diverged, verify clean, and emit baseline.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from lcm_sandbox.exceptions import SyncError
from lcm_sandbox.models import SandboxConfig, WorktreeBaseline
from lcm_sandbox.utils import git as git_utils
from lcm_sandbox.utils.logger import get_logger

logger = get_logger(__name__)

SyncStatus = Literal["in_sync", "behind", "ahead", "diverged"]


def sync_worktree(
    config: SandboxConfig, worktree_path: Path, sandbox_id: str
) -> WorktreeBaseline:
    """Fetch origin/main, rebase worktree if needed, return baseline state."""
    _fetch_origin_main(config)
    status = _determine_status(config, worktree_path)
    _handle_status(status, config, worktree_path)
    _verify_clean(worktree_path)
    _verify_branch(config, worktree_path)
    baseline = _log_baseline(config, worktree_path, sandbox_id)
    return baseline


def _fetch_origin_main(config: SandboxConfig) -> None:
    # STEP 2.1: fetch happens in the PARENT repo, not the worktree.
    result = git_utils.fetch(config.repo_path, "origin", "main")
    if not result.ok:
        raise SyncError(
            "git fetch origin main failed",
            step="2.1",
            repo_path=str(config.repo_path),
            stderr=result.stderr.strip(),
        )
    verify = git_utils.rev_parse("origin/main", config.repo_path)
    if not verify.ok or len(verify.stdout.strip()) != 40:
        raise SyncError(
            "origin/main did not resolve to a commit hash after fetch",
            step="2.1",
            repo_path=str(config.repo_path),
            output=verify.stdout.strip(),
        )


def _determine_status(config: SandboxConfig, worktree_path: Path) -> SyncStatus:
    # STEP 2.2: compare worktree HEAD against origin/main using merge-base.
    head = git_utils.rev_parse("HEAD", worktree_path)
    origin = git_utils.rev_parse("origin/main", worktree_path)
    mb = git_utils.merge_base(config.branch_name, "origin/main", worktree_path)
    if not (head.ok and origin.ok and mb.ok):
        raise SyncError(
            "failed to compute sync status",
            step="2.2",
            worktree_path=str(worktree_path),
            head_err=head.stderr.strip(),
            origin_err=origin.stderr.strip(),
            mb_err=mb.stderr.strip(),
        )
    head_sha = head.stdout.strip()
    origin_sha = origin.stdout.strip()
    base_sha = mb.stdout.strip()
    if head_sha == origin_sha:
        return "in_sync"
    if base_sha == head_sha:
        return "behind"
    if base_sha == origin_sha:
        return "ahead"
    return "diverged"


def _handle_status(
    status: SyncStatus, config: SandboxConfig, worktree_path: Path
) -> None:
    # STEP 2.3
    if status == "in_sync":
        return
    if status == "behind":
        rebase = git_utils.rebase("origin/main", worktree_path)
        if not rebase.ok:
            raise SyncError(
                "rebase onto origin/main failed; manual resolution required",
                step="2.3",
                worktree_path=str(worktree_path),
                branch_name=config.branch_name,
                stderr=rebase.stderr.strip(),
                stdout=rebase.stdout.strip(),
            )
        return
    if status == "ahead":
        raise SyncError(
            "worktree is ahead of origin/main (unpushed commits present)",
            step="2.3",
            worktree_path=str(worktree_path),
            branch_name=config.branch_name,
        )
    raise SyncError(
        "worktree branch has diverged from origin/main",
        step="2.3",
        worktree_path=str(worktree_path),
        branch_name=config.branch_name,
    )


def _verify_clean(worktree_path: Path) -> None:
    # STEP 2.4
    if not git_utils.is_clean(worktree_path):
        raise SyncError(
            "worktree has uncommitted changes after sync",
            step="2.4",
            worktree_path=str(worktree_path),
        )


def _verify_branch(config: SandboxConfig, worktree_path: Path) -> None:
    # STEP 2.5
    result = git_utils.current_branch(worktree_path)
    if not result.ok:
        raise SyncError(
            "could not determine current branch",
            step="2.5",
            worktree_path=str(worktree_path),
            stderr=result.stderr.strip(),
        )
    current = result.stdout.strip()
    if current != config.branch_name:
        raise SyncError(
            "worktree is on the wrong branch",
            step="2.5",
            worktree_path=str(worktree_path),
            expected=config.branch_name,
            actual=current,
        )


def _log_baseline(
    config: SandboxConfig, worktree_path: Path, sandbox_id: str
) -> WorktreeBaseline:
    # STEP 2.6
    head = git_utils.rev_parse("HEAD", worktree_path)
    log = git_utils.log_one("%H %s", worktree_path)
    if not (head.ok and log.ok):
        raise SyncError(
            "failed to capture baseline state",
            step="2.6",
            worktree_path=str(worktree_path),
        )
    baseline = WorktreeBaseline(
        sandbox_id=sandbox_id,
        worktree_path=worktree_path,
        branch_name=config.branch_name,
        latest_commit=head.stdout.strip(),
        latest_commit_msg=log.stdout.strip(),
        sync_timestamp=datetime.now(timezone.utc),
    )
    logger.info(
        "sync_complete",
        extra={
            "phase": 2,
            "step": "2.6",
            "sandbox_id": sandbox_id,
            "latest_commit": baseline.latest_commit,
            "branch": config.branch_name,
        },
    )
    return baseline
