"""Unit tests for scripts/rm-shim.sh.

The shim is meant to be installed at /usr/local/bin/rm inside the
lcm-hermes-agent container. On the host (test machine) we can't replace
/bin/rm, but we can still exercise the safelist logic by invoking the script
directly and overriding the safelist to point at a tmp_path-based safe area.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SHIM = Path(__file__).resolve().parents[2] / "scripts" / "rm-shim.sh"


def _shim_with_safelist(*safe_dirs: str) -> str:
    """Return a temp shim copy whose SAFE_PREFIXES array is rewritten to safe_dirs.

    We rewrite by sed-substituting the array literal. Safer than env-var
    plumbing for this test (the shim is deliberately hardcoded so a runtime
    env var can't widen it from inside the container).
    """
    body = SHIM.read_text()
    quoted = "\n".join(f'  "{d}"' for d in safe_dirs)
    new = body.replace(
        '''SAFE_PREFIXES=(
  "/workspace"
  "/tmp"
  "/var/tmp"
  "/home/aiagent/.cache"
  "/home/aiagent/.local"
)''',
        f'SAFE_PREFIXES=(\n{quoted}\n)',
    )
    out = SHIM.parent.parent / "lcm_sandbox" / "tests" / ".rm-shim-test.sh"
    out.write_text(new)
    out.chmod(0o755)
    return str(out)


def _run(shim_path: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", shim_path, *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_shim_exists_and_is_executable() -> None:
    assert SHIM.exists()
    assert os.access(SHIM, os.X_OK)


def test_safe_path_is_deleted(tmp_path: Path) -> None:
    safe = tmp_path / "ws"
    safe.mkdir()
    victim = safe / "delete-me.txt"
    victim.write_text("x")

    shim = _shim_with_safelist(str(safe))
    try:
        proc = _run(shim, str(victim))
        assert proc.returncode == 0, proc.stderr
        assert not victim.exists()
    finally:
        Path(shim).unlink(missing_ok=True)


def test_unsafe_path_is_rejected(tmp_path: Path) -> None:
    safe = tmp_path / "ws"
    safe.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x")

    shim = _shim_with_safelist(str(safe))
    try:
        proc = _run(shim, str(outside))
        assert proc.returncode != 0
        assert "blocked" in proc.stderr
        assert outside.exists(), "rm-shim must not have invoked the underlying rm"
    finally:
        Path(shim).unlink(missing_ok=True)


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    safe = tmp_path / "ws"
    safe.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("important")

    # Symlink inside the safe area pointing OUT.
    escape = safe / "escape.txt"
    escape.symlink_to(outside)

    shim = _shim_with_safelist(str(safe))
    try:
        proc = _run(shim, str(escape))
        # readlink -f resolves the symlink to outside the safelist → reject.
        assert proc.returncode != 0
        assert outside.exists()
    finally:
        Path(shim).unlink(missing_ok=True)


def test_flags_are_passed_through(tmp_path: Path) -> None:
    safe = tmp_path / "ws"
    safe.mkdir()
    sub = safe / "subdir"
    sub.mkdir()
    (sub / "a.txt").write_text("x")

    shim = _shim_with_safelist(str(safe))
    try:
        proc = _run(shim, "-rf", str(sub))
        assert proc.returncode == 0, proc.stderr
        assert not sub.exists()
    finally:
        Path(shim).unlink(missing_ok=True)


def test_double_dash_treats_following_as_paths(tmp_path: Path) -> None:
    """`rm -- /path/named/-rf` should validate `/path/named/-rf` as a path."""
    safe = tmp_path / "ws"
    safe.mkdir()
    # File whose name starts with a dash — verifies `--` end-of-options handling.
    weird = safe / "-rf"
    weird.write_text("x")

    shim = _shim_with_safelist(str(safe))
    try:
        proc = _run(shim, "--", str(weird))
        assert proc.returncode == 0, proc.stderr
        assert not weird.exists()
    finally:
        Path(shim).unlink(missing_ok=True)
