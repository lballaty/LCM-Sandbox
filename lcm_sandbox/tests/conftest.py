"""Shared fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from lcm_sandbox.models import AllowedPaths, SandboxConfig
from lcm_sandbox.utils.shell import CommandResult


def ok(stdout: str = "", stderr: str = "", args: list[str] | None = None) -> CommandResult:
    return CommandResult(
        args=args or ["git"],
        returncode=0,
        stdout=stdout,
        stderr=stderr,
        cwd=None,
    )


def fail(stderr: str = "", returncode: int = 1, args: list[str] | None = None) -> CommandResult:
    return CommandResult(
        args=args or ["git"],
        returncode=returncode,
        stdout="",
        stderr=stderr,
        cwd=None,
    )


@pytest.fixture
def repo_dir(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    return repo


@pytest.fixture
def worktree_dir(tmp_path: Path) -> Path:
    wt = tmp_path / "worktree"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: /path/to/parent/.git/worktrees/x\n")
    return wt


@pytest.fixture
def sandbox_config(repo_dir: Path) -> SandboxConfig:
    return SandboxConfig(
        plan_id="plan_test123",
        run_id="run_test456",
        repo_path=repo_dir,
        branch_name="feature/test",
        allowed_paths=AllowedPaths(write=["src/"], read=["*"]),
        timeout_minutes=60,
        colima_profile="LCM-Dev",
    )
