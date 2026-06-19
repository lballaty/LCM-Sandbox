"""
File: lcm_sandbox/tests/test_control_plane.py
Description: Unit tests for the Phase A control-plane utilities.
Author: Libor Ballaty <libor@arionetworks.com>
Created: 2026-06-19
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from lcm_sandbox.core.control_plane import (
    SCHEMA_VERSION,
    ControlPaths,
    EventLogger,
    Heartbeat,
    InboxWatcher,
    OutboxWriter,
    StatusWriter,
    load_plan,
)


# ---------- fixtures ----------

@pytest.fixture
def paths(tmp_path: Path) -> ControlPaths:
    p = ControlPaths(root=tmp_path / "control")
    p.ensure_layout()
    return p


@pytest.fixture
def writer(paths: ControlPaths) -> StatusWriter:
    return StatusWriter(paths, run_id="run_test")


@pytest.fixture
def logger(paths: ControlPaths) -> EventLogger:
    return EventLogger(paths)


# ---------- ControlPaths ----------

class TestControlPaths:
    def test_layout_is_idempotent(self, paths: ControlPaths) -> None:
        paths.ensure_layout()
        paths.ensure_layout()  # twice; must not error
        assert paths.plan_dir.exists()
        assert paths.inbox_dir.exists()
        assert paths.outbox_dir.exists()
        assert paths.hermes_dir.exists()

    def test_expected_subpaths(self, paths: ControlPaths) -> None:
        assert paths.plan_json == paths.root / "plan" / "plan.json"
        assert paths.status_file == paths.root / "status.json"
        assert paths.events_file == paths.root / "events.jsonl"


# ---------- StatusWriter ----------

class TestStatusWriter:
    def test_initial_state(self, writer: StatusWriter, paths: ControlPaths) -> None:
        state = writer.update()  # write current with no fields
        assert paths.status_file.exists()
        assert state["schema_version"] == SCHEMA_VERSION
        assert state["run_id"] == "run_test"
        assert state["phase"] == "starting"
        assert state["heartbeat"]

    def test_atomic_write_produces_valid_json(self, writer: StatusWriter,
                                              paths: ControlPaths) -> None:
        writer.update(phase="executing", current_step="step 1")
        # No partial files left behind
        assert list(paths.root.glob("status.json.*.tmp")) == []
        data = json.loads(paths.status_file.read_text())
        assert data["phase"] == "executing"
        assert data["current_step"] == "step 1"

    def test_phase_transitions(self, writer: StatusWriter) -> None:
        writer.update(phase="executing")
        writer.update(phase="blocked", blocked_on="m_001")
        writer.update(phase="executing", blocked_on=None)
        final = writer.snapshot()
        assert final["phase"] == "executing"
        assert final["blocked_on"] is None

    def test_manifest_commits_accumulate(self, writer: StatusWriter,
                                         paths: ControlPaths) -> None:
        writer.add_commit("aaa111")
        writer.add_commit("bbb222")
        writer.add_commit("aaa111")  # dup; should not duplicate
        data = json.loads(paths.status_file.read_text())
        assert data["manifest"]["commits"] == ["aaa111", "bbb222"]

    def test_files_changed_accumulate(self, writer: StatusWriter,
                                       paths: ControlPaths) -> None:
        writer.add_file_changed("src/a.py")
        writer.add_file_changed("src/b.py")
        writer.add_file_changed("src/a.py")  # dup
        data = json.loads(paths.status_file.read_text())
        assert data["manifest"]["files_changed"] == ["src/a.py", "src/b.py"]

    def test_concurrent_updates(self, writer: StatusWriter,
                                paths: ControlPaths) -> None:
        """Status writes are thread-safe."""
        def worker(i: int) -> None:
            for j in range(20):
                writer.add_file_changed(f"f_{i}_{j}.py")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        data = json.loads(paths.status_file.read_text())
        # 4 workers × 20 files = 80 unique entries
        assert len(data["manifest"]["files_changed"]) == 80


# ---------- EventLogger ----------

class TestEventLogger:
    def test_append_known_type(self, logger: EventLogger, paths: ControlPaths) -> None:
        seq = logger.append("launched")
        assert seq == 1
        line = paths.events_file.read_text().strip()
        record = json.loads(line)
        assert record["seq"] == 1
        assert record["type"] == "launched"
        assert record["payload"] == {}

    def test_seq_is_monotonic(self, logger: EventLogger) -> None:
        s1 = logger.append("launched")
        s2 = logger.append("plan_loaded", {"plan_id": "x"})
        s3 = logger.append("step_started", {"step": 1})
        assert (s1, s2, s3) == (1, 2, 3)

    def test_seq_recovers_after_restart(self, paths: ControlPaths) -> None:
        l1 = EventLogger(paths)
        l1.append("launched")
        l1.append("plan_loaded")
        # New logger reading the same file
        l2 = EventLogger(paths)
        s = l2.append("step_started")
        assert s == 3

    def test_unknown_type_tolerated_with_warning(self, logger: EventLogger,
                                                  paths: ControlPaths,
                                                  capsys: pytest.CaptureFixture) -> None:
        logger.append("invented_event_type")
        captured = capsys.readouterr()
        assert "warning: unknown event type" in captured.out
        # but still appended
        lines = paths.events_file.read_text().strip().split("\n")
        assert json.loads(lines[0])["type"] == "invented_event_type"

    def test_tail_returns_only_new(self, logger: EventLogger) -> None:
        logger.append("launched")
        logger.append("plan_loaded")
        logger.append("step_started")
        new = list(logger.tail(since_seq=1))
        assert [e["seq"] for e in new] == [2, 3]

    def test_concurrent_append(self, logger: EventLogger,
                                paths: ControlPaths) -> None:
        def worker() -> None:
            for _ in range(25):
                logger.append("heartbeat")

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        seqs = [json.loads(l)["seq"] for l in
                paths.events_file.read_text().strip().split("\n")]
        # 4 × 25 = 100 events, all unique seqs, monotonic
        assert len(seqs) == 100
        assert seqs == sorted(set(seqs))


# ---------- OutboxWriter / InboxWatcher ----------

class TestOutboxInbox:
    def test_outbox_write_and_read(self, paths: ControlPaths) -> None:
        ob = OutboxWriter(paths)
        msg_id = ob.next_msg_id()
        path = ob.write(msg_id=msg_id, type_="ask",
                        question="continue?", options=["yes", "no"])
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["msg_id"] == msg_id
        assert data["type"] == "ask"
        assert data["question"] == "continue?"

    def test_outbox_msg_ids_are_unique(self, paths: ControlPaths) -> None:
        ob = OutboxWriter(paths)
        ids = {ob.next_msg_id() for _ in range(50)}
        assert len(ids) == 50

    def test_inbox_poll_returns_new_only(self, paths: ControlPaths) -> None:
        ib = InboxWatcher(paths)
        # Drop two messages
        (paths.inbox_dir / "m_000001.json").write_text(
            json.dumps({"msg_id": "m_000001", "type": "answer", "value": "yes"}))
        (paths.inbox_dir / "m_000002.json").write_text(
            json.dumps({"msg_id": "m_000002", "type": "answer", "value": "no"}))
        new1 = ib.poll()
        assert {m["msg_id"] for m in new1} == {"m_000001", "m_000002"}
        # Second poll: nothing new
        assert ib.poll() == []
        # Add another
        (paths.inbox_dir / "m_000003.json").write_text(
            json.dumps({"msg_id": "m_000003", "type": "answer", "value": "yes"}))
        new2 = ib.poll()
        assert [m["msg_id"] for m in new2] == ["m_000003"]

    def test_inbox_wait_for_returns_on_arrival(self, paths: ControlPaths) -> None:
        ib = InboxWatcher(paths)
        msg_id = "m_target"

        def writer_thread() -> None:
            time.sleep(0.2)
            (paths.inbox_dir / f"{msg_id}.json").write_text(
                json.dumps({"msg_id": msg_id, "type": "answer", "value": "delivered"}))

        threading.Thread(target=writer_thread, daemon=True).start()
        result = ib.wait_for(msg_id, timeout_seconds=3.0,
                             poll_interval_seconds=0.05)
        assert result is not None
        assert result["value"] == "delivered"

    def test_inbox_wait_for_times_out(self, paths: ControlPaths) -> None:
        ib = InboxWatcher(paths)
        result = ib.wait_for("never_arrives", timeout_seconds=0.3,
                             poll_interval_seconds=0.05)
        assert result is None


# ---------- Heartbeat ----------

class TestHeartbeat:
    def test_heartbeat_advances(self, writer: StatusWriter,
                                 paths: ControlPaths) -> None:
        hb = Heartbeat(writer, interval_seconds=0.1)
        hb.start()
        try:
            time.sleep(0.35)  # ~3 heartbeats
        finally:
            hb.stop()
        data = json.loads(paths.status_file.read_text())
        assert data["heartbeat"]  # exists


# ---------- load_plan ----------

class TestLoadPlan:
    def test_round_trip(self, paths: ControlPaths) -> None:
        plan = {
            "schema_version": "1",
            "plan_id": "plan_xyz",
            "run_id": "run_xyz",
            "repo_kind": "existing",
        }
        paths.plan_dir.mkdir(parents=True, exist_ok=True)
        paths.plan_json.write_text(json.dumps(plan))
        loaded = load_plan(paths)
        assert loaded == plan

    def test_missing_raises(self, paths: ControlPaths) -> None:
        with pytest.raises(FileNotFoundError):
            load_plan(paths)
