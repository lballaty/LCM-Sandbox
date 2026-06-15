# File: lcm_sandbox/tests/test_docker_launcher.py
# Description: Unit tests for Phase 4 docker_launcher (STEP 4.1-4.4) plus an
#              opt-in integration test that exercises a real container launch.
# Author: Libor Ballaty <libor@arionetworks.com>
# Created: 2026-06-12

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from lcm_sandbox.core import docker_launcher
from lcm_sandbox.core.docker_launcher import (
    DEFAULT_IMAGE_TAG,
    HERMES_INTERNAL_PORT,
    _build_run_argv,
    launch_container,
)
from lcm_sandbox.models import AllowedPaths, SandboxConfig
from lcm_sandbox.utils.shell import CommandResult


def _ok(stdout: str = "", stderr: str = "") -> CommandResult:
    return CommandResult(args=["docker"], returncode=0, stdout=stdout, stderr=stderr, cwd=None)


def _fail(stderr: str = "boom", code: int = 1) -> CommandResult:
    return CommandResult(args=["docker"], returncode=code, stdout="", stderr=stderr, cwd=None)


@pytest.fixture
def cfg(tmp_path: Path) -> SandboxConfig:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    return SandboxConfig(
        plan_id="plan_abc",
        run_id="run_xyz",
        repo_path=repo,
        branch_name="feature/x",
        allowed_paths=AllowedPaths(write=["src/"], read=["*"]),
        timeout_minutes=60,
    )


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    wt = tmp_path / "wt"
    wt.mkdir()
    return wt


def test_build_run_argv_contains_required_flags(cfg, worktree):
    argv = _build_run_argv(
        sandbox_config=cfg,
        image_tag=DEFAULT_IMAGE_TAG,
        worktree_path=worktree,
        hermes_persona=None,
        mcp_url=None,
        mcp_token=None,
        model_provider=None,
        model_key=None,
    )
    assert argv[0] == "docker"
    assert argv[1] == "run"
    assert "-d" in argv
    assert "--name" in argv and "run_xyz" in argv
    assert "--cap-drop=ALL" in argv
    # Volume mount
    assert any(a.startswith(f"{worktree}:/workspace") for a in argv), argv
    # Env vars
    flat = " ".join(argv)
    assert "PLAN_ID=plan_abc" in flat
    assert "RUN_ID=run_xyz" in flat
    assert "SANDBOX_ID=run_xyz" in flat
    assert "BRANCH_NAME=feature/x" in flat
    assert "TIMEOUT_MINUTES=60" in flat
    # ALLOWED_PATHS is compact JSON
    assert '"write":["src/"]' in flat
    # Image is last positional
    assert argv[-1] == DEFAULT_IMAGE_TAG
    # No --rm (we keep the container for capture)
    assert "--rm" not in argv


def test_build_run_argv_propagates_hermes_env(cfg, worktree):
    argv = _build_run_argv(
        sandbox_config=cfg,
        image_tag="lcm-hermes-agent:latest",
        worktree_path=worktree,
        hermes_persona="config-auditor",
        mcp_url="http://host:9700/mcp",
        mcp_token="tok-123",
        model_provider="anthropic",
        model_key="claude-opus-4-7",
    )
    flat = " ".join(argv)
    assert "HERMES_PERSONA=config-auditor" in flat
    assert "MCP_SERVER_URL=http://host:9700/mcp" in flat
    assert "MCP_TOKEN=tok-123" in flat
    assert "MODEL_PROVIDER=anthropic" in flat
    assert "MODEL_KEY=claude-opus-4-7" in flat


def test_build_run_argv_omits_hermes_env_when_absent(cfg, worktree):
    argv = _build_run_argv(
        sandbox_config=cfg,
        image_tag=DEFAULT_IMAGE_TAG,
        worktree_path=worktree,
        hermes_persona=None,
        mcp_url=None,
        mcp_token=None,
        model_provider=None,
        model_key=None,
    )
    flat = " ".join(argv)
    assert "HERMES_PERSONA" not in flat
    assert "MCP_SERVER_URL" not in flat
    assert "MODEL_KEY" not in flat


def test_launch_failure_returns_structured_error(cfg, worktree):
    with patch.object(docker_launcher, "run") as mock_run:
        mock_run.return_value = _fail(stderr="image not found")
        result = launch_container(
            cfg, image_tag=DEFAULT_IMAGE_TAG, worktree_path=worktree,
        )
    assert result.status == "failed"
    assert result.container_id is None
    assert "image not found" in (result.error or "")


def test_launch_missing_worktree_raises(cfg, tmp_path):
    from lcm_sandbox.exceptions import DockerLaunchError
    with pytest.raises(DockerLaunchError):
        launch_container(
            cfg,
            image_tag=DEFAULT_IMAGE_TAG,
            worktree_path=tmp_path / "does-not-exist",
        )


def test_launch_succeeds_when_health_poll_reports_up(cfg, worktree):
    # First call: `docker run -d ...` returns a container id.
    # Subsequent calls: `docker ps --filter ...` returns "Up 2 seconds".
    calls = {"n": 0}

    def fake_run(args, **kwargs):
        calls["n"] += 1
        if args[:2] == ["docker", "run"]:
            return _ok(stdout="abc123def456\n")
        if args[:3] == ["docker", "ps", "--filter"]:
            return _ok(stdout="Up 2 seconds\n")
        return _ok()

    with patch.object(docker_launcher, "run", side_effect=fake_run):
        result = launch_container(
            cfg, image_tag=DEFAULT_IMAGE_TAG, worktree_path=worktree,
        )
    assert result.status == "running"
    assert result.container_id == "abc123def456"
    assert result.hermes_api_port == HERMES_INTERNAL_PORT


def test_launch_fails_when_health_never_reaches_up(cfg, worktree, monkeypatch):
    # Make the poll loop tight so the test runs quickly.
    monkeypatch.setattr(docker_launcher, "HEALTH_POLL_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(docker_launcher, "HEALTH_POLL_INTERVAL_SECONDS", 0.05)

    def fake_run(args, **kwargs):
        if args[:2] == ["docker", "run"]:
            return _ok(stdout="cid\n")
        if args[:3] == ["docker", "ps", "--filter"]:
            return _ok(stdout="Created\n")  # never reaches Up
        if args[:2] == ["docker", "logs"]:
            return _ok(stdout="startup output...\n")
        return _ok()

    with patch.object(docker_launcher, "run", side_effect=fake_run):
        result = launch_container(
            cfg, image_tag=DEFAULT_IMAGE_TAG, worktree_path=worktree,
        )
    assert result.status == "failed"
    assert "did not reach Up" in (result.error or "")


# ---------------------------------------------------------------------------
# Integration: actually launches the real image. Skipped unless the image is
# present locally (built by scripts/build-hermes-image.sh).
# ---------------------------------------------------------------------------
def _image_present(tag: str) -> bool:
    if not shutil.which("docker"):
        return False
    p = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", tag],
        capture_output=True, text=True,
    )
    return p.returncode == 0


@pytest.mark.integration
@pytest.mark.skipif(
    not _image_present(DEFAULT_IMAGE_TAG),
    reason=f"{DEFAULT_IMAGE_TAG} not present locally",
)
def test_integration_launch_and_teardown(tmp_path):
    """End-to-end: launch container without persona, verify hermes CLI works, tear down."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    wt = tmp_path / "wt"
    wt.mkdir()

    cfg = SandboxConfig(
        plan_id="itest",
        run_id="itest_dl_" + os.urandom(3).hex(),
        repo_path=repo,
        branch_name="itest",
        allowed_paths=AllowedPaths(write=[], read=["*"]),
        timeout_minutes=15,
    )
    result = launch_container(
        cfg, image_tag=DEFAULT_IMAGE_TAG, worktree_path=wt,
    )
    try:
        assert result.status == "running", result.error
        # hermes binary should be on PATH inside the container.
        p = subprocess.run(
            ["docker", "exec", "--user", "aiagent", cfg.run_id,
             "bash", "-lc", "hermes --version"],
            capture_output=True, text=True, timeout=30,
        )
        assert p.returncode == 0, p.stderr
    finally:
        subprocess.run(["docker", "rm", "-f", cfg.run_id], capture_output=True)
