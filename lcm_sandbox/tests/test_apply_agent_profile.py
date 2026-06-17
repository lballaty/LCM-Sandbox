"""Unit tests for scripts/apply_agent_profile.py."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "apply_agent_profile.py"
TEMPLATES = Path(__file__).resolve().parents[1] / "templates" / "agent_profiles"


def _run(target: Path, profile: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--profile", profile, "--target", str(target)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_script_exists_and_executable() -> None:
    assert SCRIPT.exists()


def test_templates_exist_and_parse() -> None:
    for name in ("permissive.json", "standard.json"):
        p = TEMPLATES / name
        assert p.exists(), f"missing template: {p}"
        json.loads(p.read_text())  # raises on bad JSON


def test_permissive_writes_expected_settings(tmp_path: Path) -> None:
    proc = _run(tmp_path, "permissive")
    assert proc.returncode == 0, proc.stderr

    cfg = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert cfg["permissions"]["defaultMode"] == "bypassPermissions"
    assert cfg["skipDangerousModePermissionPrompt"] is True
    assert cfg["skipAutoPermissionPrompt"] is True
    assert cfg["skipWebFetchPreflight"] is True
    assert cfg["enableAllProjectMcpServers"] is True
    assert cfg["disableAllHooks"] is True
    assert cfg["fileCheckpointingEnabled"] is False


def test_standard_writes_expected_settings(tmp_path: Path) -> None:
    proc = _run(tmp_path, "standard")
    assert proc.returncode == 0, proc.stderr

    cfg = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert cfg["permissions"]["defaultMode"] == "acceptEdits"
    assert "Bash(*)" in cfg["permissions"]["allow"]
    assert cfg["skipDangerousModePermissionPrompt"] is True
    assert cfg["disableAllHooks"] is True


def test_unknown_profile_rejected(tmp_path: Path) -> None:
    proc = _run(tmp_path, "weird-profile")
    assert proc.returncode != 0
    # argparse exits with 2 on bad --choices arg
    assert "choose from" in proc.stderr or "invalid choice" in proc.stderr


def test_overwrites_existing_settings(tmp_path: Path) -> None:
    """Per SANDBOX-AGENT-CONFIG.md the profile must be the only source of truth."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    stale = claude_dir / "settings.json"
    stale.write_text(json.dumps({"permissions": {"defaultMode": "stale"}}))

    proc = _run(tmp_path, "permissive")
    assert proc.returncode == 0, proc.stderr

    cfg = json.loads(stale.read_text())
    assert cfg["permissions"]["defaultMode"] == "bypassPermissions"
    assert "skipDangerousModePermissionPrompt" in cfg


def test_template_dir_override(tmp_path: Path) -> None:
    """LCM_PROFILE_TEMPLATE_DIR should pick up an alternate template location."""
    alt = tmp_path / "alt"
    alt.mkdir()
    (alt / "permissive.json").write_text(
        json.dumps({"permissions": {"defaultMode": "marker-value"}})
    )

    target = tmp_path / "home"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--profile", "permissive", "--target", str(target)],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "LCM_PROFILE_TEMPLATE_DIR": str(alt)},
    )
    assert proc.returncode == 0, proc.stderr
    cfg = json.loads((target / ".claude" / "settings.json").read_text())
    assert cfg["permissions"]["defaultMode"] == "marker-value"
