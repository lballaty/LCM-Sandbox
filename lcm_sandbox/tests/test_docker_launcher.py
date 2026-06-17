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
    parse_egress_allowlist,
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
    assert "--security-opt=no-new-privileges" in argv
    assert "--read-only" in argv
    # When no egress allowlist is set, the env var must NOT be present.
    flat_envs = [argv[i + 1] for i, a in enumerate(argv) if a == "-e"]
    assert not any(e.startswith("LCM_EGRESS_ALLOWLIST=") for e in flat_envs)
    # Each --tmpfs flag is a separate pair: ("--tmpfs", "<spec>").
    tmpfs_specs = [argv[i + 1] for i, a in enumerate(argv) if a == "--tmpfs"]
    assert any(s.startswith("/tmp:") for s in tmpfs_specs), tmpfs_specs
    assert any(s.startswith("/var/tmp:") for s in tmpfs_specs), tmpfs_specs
    assert any(s.startswith("/run:") for s in tmpfs_specs), tmpfs_specs
    assert any(s.startswith("/home/aiagent/.cache:") for s in tmpfs_specs), tmpfs_specs
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


@pytest.mark.integration
@pytest.mark.skipif(
    not _image_present(DEFAULT_IMAGE_TAG),
    reason=f"{DEFAULT_IMAGE_TAG} not present locally",
)
def test_integration_hardening_assertions(tmp_path):
    """Validate the hardening flags actually take effect at container runtime:

      - /workspace is writable.
      - $HOME (aiagent) and host paths NOT explicitly mounted are not writable.
      - rootfs (`/`) is read-only.
      - git push is blocked by the installed pre-push hook.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / ".git" / "config").write_text("[core]\n\trepositoryformatversion = 0\n")
    wt = tmp_path / "wt"
    wt.mkdir()

    cfg = SandboxConfig(
        plan_id="itest",
        run_id="itest_harden_" + os.urandom(3).hex(),
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
        name = cfg.run_id

        # /workspace is writable.
        p = subprocess.run(
            ["docker", "exec", "--user", "aiagent", name,
             "bash", "-lc", "touch /workspace/.write-probe && echo OK"],
            capture_output=True, text=True, timeout=30,
        )
        assert p.returncode == 0, p.stderr

        # rootfs is read-only: writing to / fails with EROFS.
        p = subprocess.run(
            ["docker", "exec", "--user", "aiagent", name,
             "bash", "-lc", "touch /root-probe 2>&1 || echo ROFS"],
            capture_output=True, text=True, timeout=30,
        )
        assert "Read-only" in p.stdout or "ROFS" in p.stdout, p.stdout

        # /etc is read-only too.
        p = subprocess.run(
            ["docker", "exec", "--user", "aiagent", name,
             "bash", "-lc", "touch /etc/probe 2>&1 || echo ROFS"],
            capture_output=True, text=True, timeout=30,
        )
        assert "Read-only" in p.stdout or "ROFS" in p.stdout, p.stdout

        # /tmp is writable (tmpfs).
        p = subprocess.run(
            ["docker", "exec", "--user", "aiagent", name,
             "bash", "-lc", "touch /tmp/probe && echo OK"],
            capture_output=True, text=True, timeout=30,
        )
        assert "OK" in p.stdout, p.stdout

        # Pre-push hook present and rejects pushes.
        p = subprocess.run(
            ["docker", "exec", "--user", "aiagent", name,
             "bash", "-lc",
             "test -x /workspace/.git/hooks/pre-push && "
             "/workspace/.git/hooks/pre-push 2>&1; echo EXIT=$?"],
            capture_output=True, text=True, timeout=30,
        )
        assert "pushes are blocked" in p.stdout, p.stdout
        assert "EXIT=1" in p.stdout, p.stdout
    finally:
        subprocess.run(["docker", "rm", "-f", cfg.run_id], capture_output=True)


# ── Egress allowlist tests ──────────────────────────────────────────────────


def test_parse_egress_allowlist_empty():
    assert parse_egress_allowlist(None) == []
    assert parse_egress_allowlist('') == []
    assert parse_egress_allowlist('   ') == []


def test_parse_egress_allowlist_host_only():
    assert parse_egress_allowlist('api.openai.com') == [('api.openai.com', None)]


def test_parse_egress_allowlist_host_port():
    assert parse_egress_allowlist('api.openai.com:443') == [('api.openai.com', 443)]


def test_parse_egress_allowlist_multiple():
    result = parse_egress_allowlist('api.openai.com:443,registry.npmjs.org:443,deb.nodesource.com')
    assert result == [
        ('api.openai.com', 443),
        ('registry.npmjs.org', 443),
        ('deb.nodesource.com', None),
    ]


def test_parse_egress_allowlist_strips_whitespace():
    assert parse_egress_allowlist(' foo:80 , bar:443 ') == [('foo', 80), ('bar', 443)]


def test_parse_egress_allowlist_rejects_bad_host():
    with pytest.raises(ValueError):
        parse_egress_allowlist('not a host')


def test_parse_egress_allowlist_rejects_bad_port():
    with pytest.raises(ValueError):
        parse_egress_allowlist('api.openai.com:not-a-port')


def test_parse_egress_allowlist_rejects_out_of_range_port():
    with pytest.raises(ValueError):
        parse_egress_allowlist('api.openai.com:99999')


def test_build_argv_includes_egress_env_when_set(cfg, tmp_path):
    wt = tmp_path / 'wt'
    wt.mkdir()
    argv = _build_run_argv(
        sandbox_config=cfg,
        image_tag=DEFAULT_IMAGE_TAG,
        worktree_path=wt,
        hermes_persona=None,
        mcp_url=None,
        mcp_token=None,
        model_provider=None,
        model_key=None,
        egress_allowlist=[('api.openai.com', 443), ('deb.nodesource.com', None)],
    )
    envs = [argv[i + 1] for i, a in enumerate(argv) if a == '-e']
    egress = [e for e in envs if e.startswith('LCM_EGRESS_ALLOWLIST=')]
    assert len(egress) == 1
    assert egress[0] == 'LCM_EGRESS_ALLOWLIST=api.openai.com:443,deb.nodesource.com'

