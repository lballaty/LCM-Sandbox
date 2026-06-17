"""Unit tests for lcm_sandbox.core.artifact_capture."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from lcm_sandbox.core import artifact_capture
from lcm_sandbox.core.artifact_capture import (
    DEFAULT_ARTIFACTS_ROOT,
    capture_artifacts,
    cleanup_sandbox,
)
from lcm_sandbox.models import AllowedPaths, SandboxConfig, WorktreeBaseline
from lcm_sandbox.utils.shell import CommandResult


def _shell_ok(stdout: str = "", stderr: str = "") -> CommandResult:
    return CommandResult(args=["x"], returncode=0, stdout=stdout, stderr=stderr, cwd=None)


def _shell_fail(stderr: str = "no") -> CommandResult:
    return CommandResult(args=["x"], returncode=1, stdout="", stderr=stderr, cwd=None)


@pytest.fixture
def sandbox(tmp_path: Path) -> SandboxConfig:
    repo = tmp_path / "wt"
    repo.mkdir()
    (repo / ".git").mkdir()
    return SandboxConfig(
        plan_id="plan_x",
        run_id="run_y",
        repo_path=repo,
        branch_name="feature/x",
        allowed_paths=AllowedPaths(write=["src/"], read=["*"]),
        timeout_minutes=15,
    )


@pytest.fixture
def baseline(sandbox: SandboxConfig) -> WorktreeBaseline:
    return WorktreeBaseline(
        sandbox_id=sandbox.run_id,
        worktree_path=sandbox.repo_path,
        branch_name=sandbox.branch_name,
        latest_commit="baseline-sha-aaaa",
        latest_commit_msg="initial",
        sync_timestamp=datetime.utcnow(),
    )


# ── _ensure_root_layout ──────────────────────────────────────────────────────


def test_ensure_root_layout_creates_dir_and_readme(tmp_path: Path) -> None:
    root = tmp_path / "arts"
    artifact_capture._ensure_root_layout(root)
    assert root.exists()
    assert (root / "README.md").exists()
    body = (root / "README.md").read_text()
    assert "LCM-Sandbox" in body


# ── capture_artifacts: happy path ────────────────────────────────────────────


def test_capture_happy_path(sandbox: SandboxConfig, baseline: WorktreeBaseline, tmp_path: Path) -> None:
    root = tmp_path / "arts"

    def fake_run(args, **_):
        a = list(args)
        if a[:3] == ["docker", "inspect", "-f"] and "ExitCode" in a[3]:
            return _shell_ok(stdout="0\n")
        if a[:2] == ["git", "-C"] and a[3] == "rev-parse":
            return _shell_ok(stdout="final-sha-bbbb\n")
        if a[:2] == ["git", "-C"] and a[3] == "log":
            return _shell_ok(
                stdout=(
                    "commit-1\x1fAlice\x1f2026-06-16T10:00:00+00:00\x1ffirst change\n"
                    "commit-2\x1fAlice\x1f2026-06-16T10:05:00+00:00\x1fsecond change\n"
                )
            )
        if a[:2] == ["git", "-C"] and a[3] == "diff":
            return _shell_ok(stdout="--- a/foo\n+++ b/foo\n@@\n-old\n+new\n")
        return _shell_ok()

    with patch.object(artifact_capture, "run", side_effect=fake_run), \
            patch.object(artifact_capture.subprocess, "run") as mock_sp:
        mock_sp.return_value = subprocess.CompletedProcess(
            args=["docker", "logs"], returncode=0, stdout=b"stdout-bytes", stderr=b"stderr-bytes",
        )
        result = capture_artifacts(sandbox, baseline=baseline, artifacts_root=root)

    assert result.status == "captured", result.warnings
    assert result.exit_code == 0
    assert result.container_present is True
    assert result.new_commit_count == 2
    assert result.final_head == "final-sha-bbbb"
    assert result.warnings == []
    assert result.artifact_dir == root / sandbox.run_id

    # On-disk artifacts
    manifest = json.loads((result.artifact_dir / "manifest.json").read_text())
    assert manifest["status"] == "captured"
    assert manifest["new_commit_count"] == 2
    assert manifest["baseline_commit"] == "baseline-sha-aaaa"

    commits = json.loads((result.artifact_dir / "commits.json").read_text())
    assert len(commits) == 2
    assert commits[0]["sha"] == "commit-1"
    assert commits[0]["author"] == "Alice"

    diff_path = result.artifact_dir / "diff.patch"
    assert diff_path.exists()
    assert "old" in diff_path.read_text() and "new" in diff_path.read_text()


# ── No-commits path ──────────────────────────────────────────────────────────


def test_capture_no_commits(sandbox: SandboxConfig, baseline: WorktreeBaseline, tmp_path: Path) -> None:
    root = tmp_path / "arts"

    def fake_run(args, **_):
        a = list(args)
        if "ExitCode" in (a[3] if len(a) > 3 else ""):
            return _shell_ok(stdout="0\n")
        if len(a) > 3 and a[3] == "rev-parse":
            return _shell_ok(stdout="final-sha\n")
        if len(a) > 3 and a[3] == "log":
            return _shell_ok(stdout="")  # no new commits
        if len(a) > 3 and a[3] == "diff":
            return _shell_ok(stdout="")
        return _shell_ok()

    with patch.object(artifact_capture, "run", side_effect=fake_run), \
            patch.object(artifact_capture.subprocess, "run") as mock_sp:
        mock_sp.return_value = subprocess.CompletedProcess(args=["x"], returncode=0, stdout=b"", stderr=b"")
        result = capture_artifacts(sandbox, baseline=baseline, artifacts_root=root)

    assert result.status == "captured"
    assert result.new_commit_count == 0
    assert result.final_head == "final-sha"
    commits = json.loads((result.artifact_dir / "commits.json").read_text())
    assert commits == []


# ── Container missing path ──────────────────────────────────────────────────


def test_capture_no_container(sandbox: SandboxConfig, baseline: WorktreeBaseline, tmp_path: Path) -> None:
    root = tmp_path / "arts"
    (sandbox.repo_path / ".git").rmdir()  # also remove the git dir so worktree path also fails

    def fake_run(args, **_):
        a = list(args)
        if a[:2] == ["docker", "inspect"]:
            return _shell_fail(stderr="no such container")
        return _shell_ok()

    with patch.object(artifact_capture, "run", side_effect=fake_run), \
            patch.object(artifact_capture.subprocess, "run") as mock_sp:
        mock_sp.return_value = subprocess.CompletedProcess(args=["x"], returncode=0, stdout=b"", stderr=b"")
        result = capture_artifacts(sandbox, baseline=baseline, artifacts_root=root)

    assert result.status == "no_container"
    assert result.container_present is False
    assert result.exit_code is None
    assert any("not found" in w for w in result.warnings)


# ── cleanup_sandbox ──────────────────────────────────────────────────────────


def test_cleanup_removes_container_and_worktree(sandbox: SandboxConfig, tmp_path: Path) -> None:
    root = tmp_path / "arts"
    art_dir = root / sandbox.run_id
    art_dir.mkdir(parents=True)
    (art_dir / "manifest.json").write_text("{}")

    def fake_run(args, **_):
        a = list(args)
        if a[:2] == ["docker", "inspect"]:
            return _shell_ok(stdout="exited\n")
        if a[:2] == ["docker", "stop"]:
            return _shell_ok()
        if a[:2] == ["docker", "rm"]:
            return _shell_ok()
        if a[:3] == ["git", "worktree", "remove"]:
            return _shell_ok()
        return _shell_ok()

    with patch.object(artifact_capture, "run", side_effect=fake_run):
        result = cleanup_sandbox(sandbox, keep_artifacts=True, artifacts_root=root)

    assert result["actions"]["container"] == "removed"
    assert result["actions"]["worktree"] == "removed"
    assert "kept" in result["actions"]["artifacts"]


def test_cleanup_idempotent_when_already_clean(sandbox: SandboxConfig, tmp_path: Path) -> None:
    root = tmp_path / "arts"

    def fake_run(args, **_):
        a = list(args)
        if a[:2] == ["docker", "inspect"]:
            return _shell_fail(stderr="no such container")
        return _shell_ok()

    # Worktree also gone.
    import shutil as _sh
    _sh.rmtree(sandbox.repo_path)

    with patch.object(artifact_capture, "run", side_effect=fake_run):
        result = cleanup_sandbox(sandbox, keep_artifacts=True, artifacts_root=root)

    assert result["actions"]["container"] == "already-absent"
    assert result["actions"]["worktree"] == "already-absent"
    assert result["actions"]["artifacts"] == "none"


def test_cleanup_removes_artifacts_when_asked(sandbox: SandboxConfig, tmp_path: Path) -> None:
    root = tmp_path / "arts"
    art_dir = root / sandbox.run_id
    art_dir.mkdir(parents=True)
    (art_dir / "manifest.json").write_text("{}")

    def fake_run(args, **_):
        a = list(args)
        if a[:2] == ["docker", "inspect"]:
            return _shell_fail("absent")
        return _shell_ok()

    with patch.object(artifact_capture, "run", side_effect=fake_run):
        result = cleanup_sandbox(sandbox, keep_artifacts=False, artifacts_root=root)

    assert result["actions"]["artifacts"] == "removed"
    assert not art_dir.exists()


def test_cleanup_keeps_worktree_when_asked(sandbox: SandboxConfig, tmp_path: Path) -> None:
    root = tmp_path / "arts"

    def fake_run(args, **_):
        if list(args)[:2] == ["docker", "inspect"]:
            return _shell_fail("absent")
        return _shell_ok()

    with patch.object(artifact_capture, "run", side_effect=fake_run):
        result = cleanup_sandbox(sandbox, remove_worktree=False, artifacts_root=root)

    assert result["actions"]["worktree"] == "kept"
    assert sandbox.repo_path.exists()
