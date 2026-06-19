"""
File: lcm_sandbox/core/control_plane.py
Description:
    Phase A control-plane writer/reader utilities for the agentic sandbox.

    Writes status.json, appends events.jsonl, watches inbox/, writes outbox/
    per the canonical wire-format contract in SANDBOX-CONTROL-SCHEMA.md.

    Designed to be called from inside the sandbox container by either:
      - Hermes (when #112 lands), which orchestrates writes for the agent, or
      - The agent directly via the sandbox-emit CLI (lcm_sandbox.cli_emit)
        until Hermes is in place.

    AIDevOps tails the resulting files from the host filesystem
    (mount point: ~/.lcm-sandbox/runs/<sandbox-id>/control/).

Author: Libor Ballaty <libor@arionetworks.com>
Created: 2026-06-19
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


SCHEMA_VERSION = "1"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Write JSON atomically: write to temp file in same dir, then rename.

    Required because AIDevOps polls these files from the host; a partial
    write must never be visible to the poller.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2, sort_keys=False)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@dataclass
class ControlPaths:
    """Resolved on-disk layout for one sandbox run's control directory.

    Mirrors the structure declared in SANDBOX-CONTROL-SCHEMA.md.
    """

    root: Path  # /control inside the container, or ~/.lcm-sandbox/runs/<id>/ on host

    @property
    def plan_dir(self) -> Path:
        return self.root / "plan"

    @property
    def plan_json(self) -> Path:
        return self.plan_dir / "plan.json"

    @property
    def status_file(self) -> Path:
        return self.root / "status.json"

    @property
    def events_file(self) -> Path:
        return self.root / "events.jsonl"

    @property
    def inbox_dir(self) -> Path:
        return self.root / "inbox"

    @property
    def outbox_dir(self) -> Path:
        return self.root / "outbox"

    @property
    def hermes_dir(self) -> Path:
        return self.root / "hermes"

    def ensure_layout(self) -> None:
        """Create all standard subdirectories. Idempotent."""
        for d in (self.plan_dir, self.plan_dir / "inputs", self.inbox_dir,
                  self.outbox_dir, self.hermes_dir):
            d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Status writer
# ---------------------------------------------------------------------------

class StatusWriter:
    """Maintains the singleton status.json file for one run.

    status.json is latest-only — every update overwrites the previous file.
    Writes are atomic (tempfile + rename) so the AIDevOps poller never
    observes a partial JSON document.
    """

    def __init__(self, paths: ControlPaths, run_id: str):
        self._paths = paths
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {
            "schema_version":  SCHEMA_VERSION,
            "run_id":          run_id,
            "phase":           "starting",
            "current_step":    "",
            "step_index":      None,
            "step_total":      None,
            "last_action_ts":  _utc_now_iso(),
            "heartbeat":       _utc_now_iso(),
            "blocked_on":      None,
            "exit_code":       None,
            "manifest": {
                "commits":       [],
                "files_changed": [],
            },
        }

    def update(self, **fields: Any) -> dict:
        """Merge fields into status and write atomically.

        Caller passes only the fields that changed; rest are preserved.
        Returns the new state.
        """
        with self._lock:
            self._state.update(fields)
            self._state["heartbeat"] = _utc_now_iso()
            if "last_action_ts" not in fields:
                self._state["last_action_ts"] = self._state["heartbeat"]
            _atomic_write_json(self._paths.status_file, self._state)
            return dict(self._state)

    def heartbeat(self) -> None:
        """Update only the heartbeat timestamp (no semantic change)."""
        with self._lock:
            self._state["heartbeat"] = _utc_now_iso()
            _atomic_write_json(self._paths.status_file, self._state)

    def add_commit(self, sha: str) -> None:
        with self._lock:
            commits = list(self._state["manifest"]["commits"])
            if sha not in commits:
                commits.append(sha)
            self._state["manifest"]["commits"] = commits
            self._state["heartbeat"] = _utc_now_iso()
            _atomic_write_json(self._paths.status_file, self._state)

    def add_file_changed(self, path: str) -> None:
        with self._lock:
            files = list(self._state["manifest"]["files_changed"])
            if path not in files:
                files.append(path)
            self._state["manifest"]["files_changed"] = files
            self._state["heartbeat"] = _utc_now_iso()
            _atomic_write_json(self._paths.status_file, self._state)

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._state)


# ---------------------------------------------------------------------------
# Event logger
# ---------------------------------------------------------------------------

class EventLogger:
    """Append-only JSONL audit log.

    Each line is a single JSON object with monotonic seq, ISO timestamp,
    event type, and payload. Sequence number is recovered from the existing
    file on startup so restarted runs continue the sequence rather than
    overwrite.
    """

    KNOWN_TYPES = frozenset({
        "launched", "plan_loaded", "step_started", "step_completed",
        "file_edited", "commit", "tool_call", "tool_refused",
        "llm_call", "policy_decision", "blocked", "unblocked",
        "heartbeat", "tasks_complete", "failed", "regression_detected",
    })

    def __init__(self, paths: ControlPaths):
        self._paths = paths
        self._lock = threading.Lock()
        self._seq = self._recover_seq()

    def _recover_seq(self) -> int:
        """Return the next seq to use, by reading the last line of the existing file."""
        f = self._paths.events_file
        if not f.exists():
            return 1
        last_seq = 0
        try:
            with f.open("r") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        if isinstance(obj.get("seq"), int):
                            last_seq = max(last_seq, obj["seq"])
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass
        return last_seq + 1

    def append(self, event_type: str, payload: dict | None = None) -> int:
        """Append an event. Returns the seq used.

        Unknown event types are written with a warning logged to stderr.
        """
        if event_type not in self.KNOWN_TYPES:
            # Tolerate unknown types per the schema's forward-compat rule,
            # but surface to stderr so authors notice typos.
            print(
                f"warning: unknown event type '{event_type}' "
                f"(known: {sorted(self.KNOWN_TYPES)})",
                flush=True,
            )

        with self._lock:
            seq = self._seq
            self._seq += 1
            record = {
                "seq":     seq,
                "ts":      _utc_now_iso(),
                "type":    event_type,
                "payload": payload or {},
            }
            self._paths.events_file.parent.mkdir(parents=True, exist_ok=True)
            with self._paths.events_file.open("a") as fh:
                fh.write(json.dumps(record) + "\n")
            return seq

    def tail(self, since_seq: int = 0) -> Iterator[dict]:
        """Read events with seq > since_seq. Generator; finite (no follow)."""
        f = self._paths.events_file
        if not f.exists():
            return
        with f.open("r") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj.get("seq"), int) and obj["seq"] > since_seq:
                    yield obj


# ---------------------------------------------------------------------------
# Outbox writer / Inbox reader
# ---------------------------------------------------------------------------

class OutboxWriter:
    """Writes Hermes → AIDevOps messages to /control/outbox/<msg-id>.json.

    Each message has a stable msg_id so AIDevOps' reply (in inbox) can
    correlate to the original.
    """

    def __init__(self, paths: ControlPaths):
        self._paths = paths
        self._counter = 0
        self._lock = threading.Lock()

    def write(self, msg_id: str, type_: str, **fields: Any) -> Path:
        record = {
            "msg_id": msg_id,
            "type":   type_,
            "ts":     _utc_now_iso(),
            **fields,
        }
        path = self._paths.outbox_dir / f"{msg_id}.json"
        _atomic_write_json(path, record)
        return path

    def next_msg_id(self) -> str:
        """Generate a stable, short, monotonic message id."""
        with self._lock:
            self._counter += 1
            return f"m_{self._counter:06d}"


class InboxWatcher:
    """Polls /control/inbox/ for AIDevOps → Hermes messages.

    Resolved messages are NOT deleted; they stay for audit. The watcher
    tracks which msg_ids it has already returned so subsequent polls only
    return new ones.
    """

    def __init__(self, paths: ControlPaths):
        self._paths = paths
        self._seen: set[str] = set()

    def poll(self) -> list[dict]:
        """Return new inbox messages since last poll. Sorted by msg_id."""
        if not self._paths.inbox_dir.exists():
            return []
        new: list[dict] = []
        for fname in sorted(p.name for p in self._paths.inbox_dir.glob("*.json")):
            if fname in self._seen:
                continue
            try:
                with (self._paths.inbox_dir / fname).open("r") as fh:
                    obj = json.load(fh)
                new.append(obj)
                self._seen.add(fname)
            except (OSError, json.JSONDecodeError):
                # Partial write or corrupted; will retry next poll
                continue
        return new

    def wait_for(self, msg_id: str, timeout_seconds: float = 1800.0,
                 poll_interval_seconds: float = 1.0) -> dict | None:
        """Block until a message with the given msg_id appears in inbox.

        Returns the message or None on timeout. Suitable for the agent's
        ask-blocking-call pattern.
        """
        deadline = time.monotonic() + timeout_seconds
        target_file = self._paths.inbox_dir / f"{msg_id}.json"
        while time.monotonic() < deadline:
            if target_file.exists():
                try:
                    with target_file.open("r") as fh:
                        obj = json.load(fh)
                    self._seen.add(target_file.name)
                    return obj
                except (OSError, json.JSONDecodeError):
                    pass
            time.sleep(poll_interval_seconds)
        return None


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------

class Heartbeat:
    """Background thread that updates status.heartbeat at a regular interval.

    Per the schema, AIDevOps marks a run `stalled` if the heartbeat is older
    than 60 seconds. Default interval is 10s, giving 5x safety margin.
    """

    def __init__(self, status_writer: StatusWriter, interval_seconds: float = 10.0):
        self._writer = status_writer
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval + 1.0)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._writer.heartbeat()
            except Exception:
                # Heartbeat failure must not crash the agent
                pass
            self._stop.wait(self._interval)


# ---------------------------------------------------------------------------
# Plan loader (convenience)
# ---------------------------------------------------------------------------

def load_plan(paths: ControlPaths) -> dict:
    """Read /control/plan/plan.json. Raises FileNotFoundError if missing."""
    return json.loads(paths.plan_json.read_text())


__all__ = [
    "SCHEMA_VERSION",
    "ControlPaths",
    "StatusWriter",
    "EventLogger",
    "OutboxWriter",
    "InboxWatcher",
    "Heartbeat",
    "load_plan",
]
