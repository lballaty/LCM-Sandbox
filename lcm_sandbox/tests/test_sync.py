"""Tests for core.sync (Phase 2 status branches)."""

from __future__ import annotations

import pytest

from lcm_sandbox.core import sync
from lcm_sandbox.exceptions import SyncError
from lcm_sandbox.tests.conftest import fail, ok


def _stub_fetch_ok(mocker) -> None:
    mocker.patch.object(sync.git_utils, "fetch", return_value=ok())
    mocker.patch.object(
        sync.git_utils,
        "rev_parse",
        side_effect=_rev_parse_factory(origin="a" * 40, head="a" * 40),
    )


def _rev_parse_factory(*, origin: str, head: str):
    """Factory that returns different shas based on ref argument."""
    def _side_effect(ref, repo_path):
        if ref == "origin/main":
            return ok(origin)
        if ref == "HEAD":
            return ok(head)
        return ok(head)
    return _side_effect


def test_in_sync(mocker, sandbox_config, worktree_dir) -> None:
    head = "a" * 40
    mocker.patch.object(sync.git_utils, "fetch", return_value=ok())
    mocker.patch.object(
        sync.git_utils, "rev_parse", side_effect=_rev_parse_factory(origin=head, head=head)
    )
    mocker.patch.object(sync.git_utils, "merge_base", return_value=ok(head))
    mocker.patch.object(sync.git_utils, "status_porcelain", return_value=ok(""))
    mocker.patch.object(sync.git_utils, "current_branch", return_value=ok("feature/test"))
    mocker.patch.object(sync.git_utils, "log_one", return_value=ok(f"{head} init"))

    baseline = sync.sync_worktree(sandbox_config, worktree_dir, "sandbox-x")
    assert baseline.latest_commit == head
    assert baseline.branch_name == "feature/test"


def test_behind_triggers_rebase(mocker, sandbox_config, worktree_dir) -> None:
    origin = "b" * 40
    head = "a" * 40
    mocker.patch.object(sync.git_utils, "fetch", return_value=ok())
    mocker.patch.object(
        sync.git_utils,
        "rev_parse",
        side_effect=_rev_parse_factory(origin=origin, head=head),
    )
    mocker.patch.object(sync.git_utils, "merge_base", return_value=ok(head))  # head == base => behind
    rebase = mocker.patch.object(sync.git_utils, "rebase", return_value=ok())
    mocker.patch.object(sync.git_utils, "status_porcelain", return_value=ok(""))
    mocker.patch.object(sync.git_utils, "current_branch", return_value=ok("feature/test"))
    mocker.patch.object(sync.git_utils, "log_one", return_value=ok(f"{head} init"))

    sync.sync_worktree(sandbox_config, worktree_dir, "sandbox-x")
    rebase.assert_called_once()


def test_ahead_aborts(mocker, sandbox_config, worktree_dir) -> None:
    origin = "b" * 40
    head = "a" * 40
    mocker.patch.object(sync.git_utils, "fetch", return_value=ok())
    mocker.patch.object(
        sync.git_utils,
        "rev_parse",
        side_effect=_rev_parse_factory(origin=origin, head=head),
    )
    mocker.patch.object(sync.git_utils, "merge_base", return_value=ok(origin))  # base == origin => ahead
    with pytest.raises(SyncError, match="ahead of origin/main"):
        sync.sync_worktree(sandbox_config, worktree_dir, "sandbox-x")


def test_diverged_aborts(mocker, sandbox_config, worktree_dir) -> None:
    origin = "b" * 40
    head = "a" * 40
    mb = "c" * 40
    mocker.patch.object(sync.git_utils, "fetch", return_value=ok())
    mocker.patch.object(
        sync.git_utils,
        "rev_parse",
        side_effect=_rev_parse_factory(origin=origin, head=head),
    )
    mocker.patch.object(sync.git_utils, "merge_base", return_value=ok(mb))
    with pytest.raises(SyncError, match="diverged"):
        sync.sync_worktree(sandbox_config, worktree_dir, "sandbox-x")


def test_rebase_conflict_aborts(mocker, sandbox_config, worktree_dir) -> None:
    origin = "b" * 40
    head = "a" * 40
    mocker.patch.object(sync.git_utils, "fetch", return_value=ok())
    mocker.patch.object(
        sync.git_utils,
        "rev_parse",
        side_effect=_rev_parse_factory(origin=origin, head=head),
    )
    mocker.patch.object(sync.git_utils, "merge_base", return_value=ok(head))
    mocker.patch.object(
        sync.git_utils, "rebase", return_value=fail("CONFLICT (content): src/x.py")
    )
    with pytest.raises(SyncError, match="rebase onto origin/main failed"):
        sync.sync_worktree(sandbox_config, worktree_dir, "sandbox-x")


def test_fetch_failure(mocker, sandbox_config, worktree_dir) -> None:
    mocker.patch.object(sync.git_utils, "fetch", return_value=fail("no network"))
    with pytest.raises(SyncError, match="fetch origin main failed"):
        sync.sync_worktree(sandbox_config, worktree_dir, "sandbox-x")
