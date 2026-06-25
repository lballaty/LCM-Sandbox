# File: lcm_sandbox/commands/scaffold.py
# Description: Deterministic scaffolding executor — reads a plan.json with a
#   scaffolding_actions[] field and executes the actions (write_file, git_init,
#   git_commit) inline on the host, emitting per-action events via the existing
#   control-plane modules so AIDevOps polling sees the same shape as Hermes runs.
#   This is RCW-4 Slice B v0: host-side execution but using the platform-native
#   event/status model. Container-isolated execution becomes a later commit that
#   swaps the action handlers for `docker exec`-based execution without changing
#   the surrounding tracking semantics.
# Author: Claude Haiku 4.5
# Created: 2026-06-25

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lcm_sandbox.core.control_plane import (
    ControlPaths,
    EventLogger,
    StatusWriter,
)

# Supported action types for the deterministic executor.
SUPPORTED_ACTIONS = frozenset({"write_file", "git_init", "git_commit"})


class ScaffoldError(Exception):
    """Raised when scaffolding execution fails."""


@dataclass
class ScaffoldResult:
    """Outcome of a scaffolding run."""

    success: bool
    target_path: Path
    actions: list[dict[str, Any]] = field(default_factory=list)
    failed_action_index: int | None = None
    error_message: str | None = None


def execute_scaffold_plan(
    plan: dict[str, Any],
    target_path: Path,
    control_paths: ControlPaths,
    *,
    run_id: str,
) -> ScaffoldResult:
    """Execute a scaffolding plan deterministically.

    Reads plan.scaffolding_actions[], creates the target directory, walks the
    actions, and updates each action's status (pending -> running -> done|failed)
    in the returned ScaffoldResult. After every transition the StatusWriter
    publishes the latest plan snapshot and the EventLogger appends a per-action
    event so AIDevOps consumers can render live progress.

    On failure the offending action is stamped `failed` with an error message,
    later actions remain `pending`, and the partial scaffold is left on disk for
    operator inspection.

    Parameters
    ----------
    plan : dict
        The plan.json content (already loaded). Must contain
        plan["scaffolding_actions"] as a non-empty list.
    target_path : Path
        Absolute directory to scaffold into. Must NOT already exist.
    control_paths : ControlPaths
        Resolved control-dir paths (already constructed with ensure_layout()).
    run_id : str
        Run identifier (used in status/event payloads).

    Returns
    -------
    ScaffoldResult
        Final state of every action and overall success flag.
    """
    actions = plan.get("scaffolding_actions")
    if not isinstance(actions, list) or not actions:
        raise ScaffoldError(
            "plan.scaffolding_actions must be a non-empty list"
        )

    # Pre-execution validation — implicit setup, not tracked as an action.
    if target_path.exists():
        raise ScaffoldError(f"target_path already exists: {target_path}")
    target_path.mkdir(parents=True, exist_ok=False)

    # Deep-copy actions so we can mutate status without aliasing the caller.
    state: list[dict[str, Any]] = [dict(a) for a in actions]
    for a in state:
        a.setdefault("status", "pending")

    status_writer = StatusWriter(control_paths, run_id)
    event_logger = EventLogger(control_paths)

    status_writer.update(state="running", scaffolding_actions=state)

    for i, action in enumerate(state):
        action["status"] = "running"
        status_writer.update(scaffolding_actions=state)
        event_logger.append(
            "step_started",
            {"index": i, "action": action.get("action"), "path": action.get("path")},
        )

        try:
            _execute_action(action, target_path)
        except Exception as err:  # noqa: BLE001 — any handler failure halts the run
            action["status"] = "failed"
            action["error_message"] = str(err)
            status_writer.update(scaffolding_actions=state, state="failed")
            event_logger.append(
                "failed",
                {"index": i, "action": action.get("action"), "error": str(err)},
            )
            return ScaffoldResult(
                success=False,
                target_path=target_path,
                actions=state,
                failed_action_index=i,
                error_message=str(err),
            )

        action["status"] = "done"
        status_writer.update(scaffolding_actions=state)
        event_logger.append(
            "step_completed",
            {"index": i, "action": action.get("action")},
        )

    status_writer.update(state="completed", scaffolding_actions=state)
    event_logger.append("tasks_complete", {"actions_completed": len(state)})

    return ScaffoldResult(
        success=True,
        target_path=target_path,
        actions=state,
    )


def _execute_action(action: dict[str, Any], target_path: Path) -> None:
    """Dispatch a single action to its handler. Raises on failure."""
    kind = action.get("action")
    if kind not in SUPPORTED_ACTIONS:
        raise ScaffoldError(f"unsupported action type: {kind!r}")

    if kind == "write_file":
        _handle_write_file(action, target_path)
    elif kind == "git_init":
        _handle_git_init(target_path)
    elif kind == "git_commit":
        _handle_git_commit(action, target_path)


def _handle_write_file(action: dict[str, Any], target_path: Path) -> None:
    rel = action.get("path")
    if not rel:
        raise ScaffoldError("write_file action missing 'path'")
    content = action.get("content", "")
    file_path = target_path / rel
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")


def _handle_git_init(target_path: Path) -> None:
    _run_git(["git", "init"], cwd=target_path)


def _handle_git_commit(action: dict[str, Any], target_path: Path) -> None:
    message = action.get("message") or "Initial scaffold"
    _run_git(["git", "add", "-A"], cwd=target_path)
    _run_git(["git", "commit", "-m", message], cwd=target_path)


def _run_git(argv: list[str], *, cwd: Path) -> None:
    """Run a git command, raising ScaffoldError on non-zero exit."""
    result = subprocess.run(
        argv,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ScaffoldError(
            f"git command failed: {' '.join(argv)} "
            f"(exit={result.returncode}): {result.stderr.strip()}"
        )


def load_plan(plan_path: Path) -> dict[str, Any]:
    """Load and parse a plan.json file."""
    if not plan_path.is_file():
        raise ScaffoldError(f"plan file not found: {plan_path}")
    with plan_path.open("r", encoding="utf-8") as f:
        return json.load(f)
