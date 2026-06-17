"""Unit tests for scripts/docker-git-hooks.sh.

The script is a thin bash installer; we exercise it via subprocess against
synthetic .git directory + .git file (worktree) layouts in pytest tmp_path.
This catches the bug where the previous inline entrypoint logic gated on
`-d .git/hooks` and silently skipped worktrees (where .git is a *file*).
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "docker-git-hooks.sh"


def _run(workspace: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), str(workspace)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_script_exists_and_is_executable() -> None:
    assert SCRIPT.exists(), f"missing {SCRIPT}"
    assert os.access(SCRIPT, os.X_OK), f"{SCRIPT} is not executable"


def test_no_git_entry_is_no_op(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    proc = _run(workspace)
    assert proc.returncode == 0
    assert "skipping hook install" in proc.stderr


def test_normal_repo_installs_pre_push_hook(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    (workspace / ".git" / "hooks").mkdir(parents=True)
    proc = _run(workspace)
    assert proc.returncode == 0, proc.stderr
    hook = workspace / ".git" / "hooks" / "pre-push"
    assert hook.exists()
    mode = hook.stat().st_mode
    assert mode & stat.S_IXUSR, "hook should be executable"
    body = hook.read_text()
    assert "pushes are blocked" in body
    assert body.startswith("#!/bin/bash")


def test_worktree_dot_git_file_resolves_linked_hooks_dir(tmp_path: Path) -> None:
    # Mimic a worktree: parent repo holds .git/worktrees/<name>/; worktree dir
    # has a .git file pointing back via a relative gitdir.
    parent_repo = tmp_path / "parent"
    worktree_link = parent_repo / ".git" / "worktrees" / "feat"
    worktree_link.mkdir(parents=True)

    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / ".git").write_text(
        f"gitdir: {worktree_link}\n"
    )

    proc = _run(workspace)
    assert proc.returncode == 0, proc.stderr

    hook = worktree_link / "hooks" / "pre-push"
    assert hook.exists()
    assert "pushes are blocked" in hook.read_text()


def test_worktree_with_relative_gitdir(tmp_path: Path) -> None:
    # Some git versions write `gitdir:` as a path relative to the worktree.
    workspace = tmp_path / "ws"
    rel_target = workspace / ".." / "parent" / ".git" / "worktrees" / "feat"
    rel_target.mkdir(parents=True)
    workspace.mkdir(exist_ok=True)
    (workspace / ".git").write_text("gitdir: ../parent/.git/worktrees/feat\n")

    proc = _run(workspace)
    assert proc.returncode == 0, proc.stderr

    hook = rel_target / "hooks" / "pre-push"
    assert hook.exists()


def test_pre_push_hook_blocks_push(tmp_path: Path) -> None:
    """Run the installed hook directly and confirm it exits non-zero with the expected message."""
    workspace = tmp_path / "ws"
    (workspace / ".git" / "hooks").mkdir(parents=True)
    proc = _run(workspace)
    assert proc.returncode == 0

    hook = workspace / ".git" / "hooks" / "pre-push"
    push_proc = subprocess.run(
        ["bash", str(hook)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert push_proc.returncode != 0
    assert "pushes are blocked" in push_proc.stderr
