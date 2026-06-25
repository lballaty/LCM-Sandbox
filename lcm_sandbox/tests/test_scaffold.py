"""Unit tests for lcm_sandbox.commands.scaffold (RCW-4 Slice B v0).

Covers happy path, forced-failure mid-plan, and event emission via the
existing control-plane modules so that AIDevOps polling sees the same
shape as a Hermes run.
"""
# File: lcm_sandbox/tests/test_scaffold.py
# Description: Tests for the deterministic scaffolding executor.
# Author: Claude Haiku 4.5
# Created: 2026-06-25

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from lcm_sandbox.commands.scaffold import (
    ScaffoldError,
    execute_scaffold_plan,
    load_plan,
)
from lcm_sandbox.core.control_plane import ControlPaths


def _ctrl(tmp_path: Path) -> ControlPaths:
    ctrl = ControlPaths(root=tmp_path / "control")
    ctrl.ensure_layout()
    return ctrl


def _read_events(ctrl: ControlPaths) -> list[dict]:
    if not ctrl.events_file.is_file():
        return []
    return [json.loads(line) for line in ctrl.events_file.read_text().splitlines() if line.strip()]


def _read_status(ctrl: ControlPaths) -> dict:
    return json.loads(ctrl.status_file.read_text())


def test_happy_path_all_actions_done(tmp_path: Path) -> None:
    """3 write_file + git_init + git_commit → all done, files on disk, events emitted."""
    target = tmp_path / "scaffolded-repo"
    ctrl = _ctrl(tmp_path)
    plan = {
        "schema_version": "1",
        "scaffolding_actions": [
            {"action": "write_file", "path": "README.md", "content": "# scaffolded\n"},
            {"action": "write_file", "path": "AGENTS.md", "content": "# agents\n"},
            {"action": "write_file", "path": ".gitignore", "content": "node_modules/\n"},
            {"action": "git_init"},
            {"action": "git_commit", "message": "Initial scaffold"},
        ],
    }

    # Configure a local git identity for the commit (CI / fresh worktree).
    subprocess.run(["git", "config", "--global", "user.email", "test@example.com"],
                   check=False)
    subprocess.run(["git", "config", "--global", "user.name", "Test User"],
                   check=False)

    result = execute_scaffold_plan(plan, target, ctrl, run_id="test-run-happy")

    assert result.success is True
    assert result.failed_action_index is None
    assert result.error_message is None
    assert all(a["status"] == "done" for a in result.actions)

    # Files are actually on disk
    assert (target / "README.md").read_text() == "# scaffolded\n"
    assert (target / "AGENTS.md").read_text() == "# agents\n"
    assert (target / ".git").is_dir()

    # Status reflects completion
    status = _read_status(ctrl)
    assert status["state"] == "completed"

    # Events emitted in the expected shape
    events = _read_events(ctrl)
    step_starts = [e for e in events if e.get("type") == "step_started"]
    step_completes = [e for e in events if e.get("type") == "step_completed"]
    tasks_completes = [e for e in events if e.get("type") == "tasks_complete"]
    assert len(step_starts) == 5
    assert len(step_completes) == 5
    assert len(tasks_completes) == 1


def test_forced_failure_halts_and_preserves_partial(tmp_path: Path) -> None:
    """Unknown action type at index 2 → failed, prior actions done, later pending, files preserved."""
    target = tmp_path / "scaffolded-fail"
    ctrl = _ctrl(tmp_path)
    plan = {
        "schema_version": "1",
        "scaffolding_actions": [
            {"action": "write_file", "path": "a.txt", "content": "A\n"},
            {"action": "write_file", "path": "b.txt", "content": "B\n"},
            {"action": "bogus_action_type"},
            {"action": "write_file", "path": "c.txt", "content": "C\n"},
            {"action": "git_init"},
        ],
    }

    result = execute_scaffold_plan(plan, target, ctrl, run_id="test-run-fail")

    assert result.success is False
    assert result.failed_action_index == 2
    assert "unsupported action type" in (result.error_message or "")

    assert result.actions[0]["status"] == "done"
    assert result.actions[1]["status"] == "done"
    assert result.actions[2]["status"] == "failed"
    assert result.actions[2]["error_message"]
    assert result.actions[3]["status"] == "pending"
    assert result.actions[4]["status"] == "pending"

    # Partial scaffold preserved
    assert (target / "a.txt").read_text() == "A\n"
    assert (target / "b.txt").read_text() == "B\n"
    assert not (target / "c.txt").exists()
    assert not (target / ".git").exists()

    # Status reflects failure
    status = _read_status(ctrl)
    assert status["state"] == "failed"

    # `failed` event emitted with the right index
    events = _read_events(ctrl)
    failed = [e for e in events if e.get("type") == "failed"]
    assert len(failed) == 1
    assert failed[0]["payload"]["index"] == 2


def test_missing_actions_raises(tmp_path: Path) -> None:
    """Plan without scaffolding_actions[] is a setup error, not an execution failure."""
    target = tmp_path / "out"
    ctrl = _ctrl(tmp_path)
    with pytest.raises(ScaffoldError, match="non-empty list"):
        execute_scaffold_plan({"schema_version": "1"}, target, ctrl, run_id="r")


def test_target_path_exists_raises(tmp_path: Path) -> None:
    """Existing target dir is a setup error so we don't overwrite operator state."""
    target = tmp_path / "existing"
    target.mkdir()
    ctrl = _ctrl(tmp_path)
    plan = {
        "scaffolding_actions": [
            {"action": "write_file", "path": "x", "content": ""},
        ],
    }
    with pytest.raises(ScaffoldError, match="already exists"):
        execute_scaffold_plan(plan, target, ctrl, run_id="r")


def test_write_file_missing_path_raises_and_marks_failed(tmp_path: Path) -> None:
    """A malformed write_file action (no path) is treated as that action's failure."""
    target = tmp_path / "out"
    ctrl = _ctrl(tmp_path)
    plan = {
        "scaffolding_actions": [
            {"action": "write_file", "content": "no path here"},
        ],
    }
    result = execute_scaffold_plan(plan, target, ctrl, run_id="r")
    assert result.success is False
    assert result.failed_action_index == 0
    assert "missing 'path'" in (result.error_message or "")


def test_load_plan_round_trip(tmp_path: Path) -> None:
    """load_plan reads and parses a plan.json from disk."""
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps({
        "scaffolding_actions": [{"action": "git_init"}],
    }))
    loaded = load_plan(plan_file)
    assert loaded["scaffolding_actions"][0]["action"] == "git_init"


def test_load_plan_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ScaffoldError, match="plan file not found"):
        load_plan(tmp_path / "nope.json")
