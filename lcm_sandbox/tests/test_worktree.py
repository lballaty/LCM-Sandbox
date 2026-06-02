"""Tests for core.worktree (Phase 1 lifecycle)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from lcm_sandbox.core import worktree
from lcm_sandbox.exceptions import WorktreeError
from lcm_sandbox.tests.conftest import fail, ok


def test_make_sandbox_id() -> None:
    fixed = datetime(2026, 6, 1, 12, 30, 45, tzinfo=timezone.utc)
    sid = worktree.make_sandbox_id("run_abc", now=fixed)
    assert sid == "sandbox-run_abc-20260601T123045Z"


def test_worktree_path_for() -> None:
    p = worktree.worktree_path_for(Path("/repo"), "sandbox-x-y")
    assert p == Path("/repo/.sandbox-worktrees/sandbox-x-y")


class TestCreateNew:
    def _stub_create_path(self, mocker, monkeypatch) -> None:
        mocker.patch.object(
            worktree.git_utils,
            "ls_remote",
            return_value=ok("deadbeef0000 refs/heads/main"),
        )
        # Branch exists locally — short-circuits _ensure_local_branch.
        mocker.patch.object(worktree.git_utils, "show_ref", return_value=ok("ref"))
        mocker.patch.object(worktree.git_utils, "worktree_add", return_value=ok())
        mocker.patch.object(worktree.git_utils, "rev_parse", return_value=ok("abc123"))
        # The .git "file" presence check uses Path.exists; patch it on Path.
        monkeypatch.setattr(Path, "exists", lambda self: True)

    def test_happy_path(self, mocker, monkeypatch, sandbox_config) -> None:
        self._stub_create_path(mocker, monkeypatch)
        info = worktree.prepare_worktree(sandbox_config)
        assert info.reused is False
        assert info.sandbox_id.startswith(f"sandbox-{sandbox_config.run_id}-")
        assert info.worktree_path.parent == sandbox_config.repo_path / ".sandbox-worktrees"

    def test_origin_main_missing_aborts(self, mocker, sandbox_config) -> None:
        mocker.patch.object(worktree.git_utils, "ls_remote", return_value=fail("err"))
        with pytest.raises(WorktreeError, match="origin/main not found") as exc:
            worktree.prepare_worktree(sandbox_config)
        assert exc.value.context["step"] == "1.1.1"

    def test_worktree_add_failure(self, mocker, sandbox_config) -> None:
        mocker.patch.object(
            worktree.git_utils,
            "ls_remote",
            return_value=ok("deadbeef refs/heads/main"),
        )
        mocker.patch.object(worktree.git_utils, "show_ref", return_value=ok())
        mocker.patch.object(
            worktree.git_utils, "worktree_add", return_value=fail("path exists")
        )
        with pytest.raises(WorktreeError, match="git worktree add failed") as exc:
            worktree.prepare_worktree(sandbox_config)
        assert exc.value.context["step"] == "1.1.3"


class TestResetExisting:
    def test_happy_path(self, mocker, sandbox_config, worktree_dir) -> None:
        mocker.patch.object(worktree.git_utils, "rev_parse_git_dir", return_value=ok("/x"))
        mocker.patch.object(worktree.git_utils, "checkout", return_value=ok())
        mocker.patch.object(worktree.git_utils, "reset_hard", return_value=ok())
        mocker.patch.object(worktree.git_utils, "clean_fd", return_value=ok())
        mocker.patch.object(worktree.git_utils, "status_porcelain", return_value=ok(""))
        mocker.patch.object(worktree.git_utils, "branch_set_upstream", return_value=ok())
        info = worktree.prepare_worktree(
            sandbox_config, existing_worktree_path=worktree_dir
        )
        assert info.reused is True
        assert info.worktree_path == worktree_dir
        assert info.sandbox_id == worktree_dir.name

    def test_corrupted_worktree(self, mocker, sandbox_config, worktree_dir) -> None:
        mocker.patch.object(
            worktree.git_utils, "rev_parse_git_dir", return_value=fail("corrupt")
        )
        with pytest.raises(WorktreeError, match="corrupted") as exc:
            worktree.prepare_worktree(
                sandbox_config, existing_worktree_path=worktree_dir
            )
        assert exc.value.context["step"] == "1.2.1"

    def test_not_clean_after_reset(self, mocker, sandbox_config, worktree_dir) -> None:
        mocker.patch.object(worktree.git_utils, "rev_parse_git_dir", return_value=ok())
        mocker.patch.object(worktree.git_utils, "checkout", return_value=ok())
        mocker.patch.object(worktree.git_utils, "reset_hard", return_value=ok())
        mocker.patch.object(worktree.git_utils, "clean_fd", return_value=ok())
        mocker.patch.object(
            worktree.git_utils, "status_porcelain", return_value=ok(" M leftover.txt\n")
        )
        with pytest.raises(WorktreeError, match="still has changes"):
            worktree.prepare_worktree(
                sandbox_config, existing_worktree_path=worktree_dir
            )
