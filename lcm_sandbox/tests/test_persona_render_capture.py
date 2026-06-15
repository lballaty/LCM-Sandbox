# File: lcm_sandbox/tests/test_persona_render_capture.py
# Description: Unit tests for the WP-8 persona-state-renderer and persona-state-capturer.
#              Uses an in-process HTTPServer fixture to mock the AIDevOps platform REST API
#              and tmp_path for filesystem isolation.
# Author: Libor Ballaty <libor@arionetworks.com>
# Created: 2026-06-12

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from lcm_sandbox.persona.capturer import (
    CaptureConfig,
    capture_persona_mutations,
)
from lcm_sandbox.persona.renderer import (
    RenderConfig,
    render_persona,
)


# ── In-process mock API server ────────────────────────────────────────────────


class MockState:
    def __init__(self) -> None:
        self.personas: list[dict[str, Any]] = []
        self.memory: dict[int, list[dict[str, Any]]] = {}
        self.skills: dict[int, list[dict[str, Any]]] = {}
        self.proposed_posts: list[dict[str, Any]] = []
        self.next_id = 100


def _make_handler(state: MockState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a, **_kw):  # silence
            return

        def _send_json(self, code: int, body: Any) -> None:
            payload = json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):  # noqa: N802
            p = self.path
            if p == "/api/personas":
                self._send_json(200, {"ok": True, "data": state.personas})
                return
            if p.startswith("/api/personas/") and p.endswith("/memory"):
                pid = int(p.split("/")[3])
                self._send_json(200, {"ok": True, "data": state.memory.get(pid, [])})
                return
            if p.startswith("/api/personas/") and p.endswith("/skills"):
                pid = int(p.split("/")[3])
                self._send_json(200, {"ok": True, "data": state.skills.get(pid, [])})
                return
            if p.startswith("/api/personas/"):
                pid = int(p.split("/")[3])
                row = next((r for r in state.personas if r["id"] == pid), None)
                if not row:
                    self._send_json(404, {"error": "not found"})
                    return
                self._send_json(200, {"ok": True, "data": row})
                return
            self._send_json(404, {"error": f"no route: {p}"})

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            if self.path == "/api/personas/proposed-changes":
                state.next_id += 1
                row = {"id": state.next_id, **body, "state": "pending"}
                state.proposed_posts.append(row)
                self._send_json(201, {"ok": True, "data": row})
                return
            self._send_json(404, {"error": f"no route: {self.path}"})

    return Handler


@pytest.fixture
def mock_api():
    state = MockState()
    server = HTTPServer(("127.0.0.1", 0), _make_handler(state))
    port = server.server_port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{port}"
    try:
        yield state, base
    finally:
        server.shutdown()
        server.server_close()


# ── Persona repo on disk fixture ──────────────────────────────────────────────


@pytest.fixture
def persona_repo(tmp_path: Path) -> Path:
    """Build a minimal persona repo layout under tmp_path."""
    repo = tmp_path / "personas-repo"
    persona = repo / "personas" / "config-auditor"
    (persona / "skills" / "audit-config").mkdir(parents=True)
    (persona / "SOUL.md").write_text("# SOUL\nI audit configs.\n", encoding="utf-8")
    (persona / "persona.json").write_text(json.dumps({"key": "config-auditor"}), encoding="utf-8")
    (persona / "config.yaml").write_text(
        "provider:\n  name: ${MODEL_PROVIDER}\n  model: ${MODEL_KEY}\n"
        "mcp_servers:\n  - url: ${MCP_SERVER_URL}\n    token: ${MCP_TOKEN}\n",
        encoding="utf-8",
    )
    (persona / "mcp-servers.yaml").write_text("server: ${MCP_SERVER_URL}\n", encoding="utf-8")
    (persona / "skills" / "audit-config" / "SKILL.md").write_text(
        "# audit-config\nAudits configs.\n", encoding="utf-8"
    )
    return repo


@pytest.fixture
def seeded_state(mock_api):
    state, base = mock_api
    state.personas.append(
        {
            "id": 1,
            "persona_key": "config-auditor",
            "persona_dir_path": "personas/config-auditor",
            "display_name": "Config Auditor",
        }
    )
    state.memory[1] = [
        {
            "id": 11,
            "memory_type": "lesson",
            "scope_type": "persona",
            "scope_id": 1,
            "content": "audit-config skill works against managed repos",
            "state": "active",
        }
    ]
    state.skills[1] = [
        # No persona_instance skills yet — only a template (filtered out by renderer).
        {
            "id": 21,
            "skill_key": "audit-config",
            "asset_layer": "managed_template",
            "status": "active",
            "persona_id": None,
        }
    ]
    return state, base


# ── Renderer tests ────────────────────────────────────────────────────────────


def test_render_produces_expected_files_and_substitutions(seeded_state, persona_repo, tmp_path):
    state, base = seeded_state
    out = tmp_path / "out"

    cfg = RenderConfig(
        persona_key="config-auditor",
        output_dir=out,
        persona_repo_path=persona_repo,
        platform_api_base=base,
        platform_api_key="dev-key",
        mcp_token="tok-abc",
        mcp_url="http://mcp.example/mcp",
        model_provider="openrouter",
        model_key="anthropic/claude-sonnet-4",
    )
    result = render_persona(cfg)

    assert result.persona_id == 1
    assert (out / "SOUL.md").read_text().startswith("# SOUL")
    assert (out / "config.yaml").read_text()  # exists
    cfg_text = (out / "config.yaml").read_text()
    assert "openrouter" in cfg_text
    assert "anthropic/claude-sonnet-4" in cfg_text
    assert "tok-abc" in cfg_text
    assert "http://mcp.example/mcp" in cfg_text
    assert "${MCP_TOKEN}" not in cfg_text
    # .env carries the API_SERVER_KEY = MCP token (Phase 0 Finding 2 + 4)
    assert "API_SERVER_KEY=tok-abc" in (out / ".env").read_text()
    # MEMORY.md assembled from DB
    mem = (out / "MEMORY.md").read_text()
    assert "audit-config skill works against managed repos" in mem
    assert "# Memory — config-auditor" in mem


def test_render_writes_baseline_hashes(seeded_state, persona_repo, tmp_path):
    state, base = seeded_state
    out = tmp_path / "out"
    cfg = RenderConfig(
        persona_key="config-auditor",
        output_dir=out,
        persona_repo_path=persona_repo,
        platform_api_base=base,
        mcp_token="t",
    )
    result = render_persona(cfg)
    assert result.baseline_hash_file is not None
    data = json.loads(result.baseline_hash_file.read_text())
    assert data["persona_id"] == 1
    assert "config.yaml" in data["hashes"]
    assert "MEMORY.md" in data["hashes"]
    assert "SOUL.md" in data["hashes"]


# ── Capturer tests ────────────────────────────────────────────────────────────


def _render_then_baseline(seeded_state, persona_repo, tmp_path) -> Path:
    state, base = seeded_state
    out = tmp_path / "out"
    cfg = RenderConfig(
        persona_key="config-auditor",
        output_dir=out,
        persona_repo_path=persona_repo,
        platform_api_base=base,
        mcp_token="t",
    )
    render_persona(cfg)
    return out


def test_capture_detects_new_memory_entry(seeded_state, persona_repo, tmp_path):
    state, base = seeded_state
    out = _render_then_baseline(seeded_state, persona_repo, tmp_path)

    # Simulate the agent appending a new lesson without an id.
    mem_path = out / "MEMORY.md"
    text = mem_path.read_text() + "\n## Lessons\n\n- a brand new lesson learned in-run\n"
    mem_path.write_text(text)

    state.proposed_posts.clear()
    cfg = CaptureConfig(
        persona_id=1,
        scheduler_run_id=999,
        post_run_dir=out,
        platform_api_base=base,
    )
    result = capture_persona_mutations(cfg)
    adds = [c for c in result.proposed_changes if c.change_type == "memory_add"]
    assert len(adds) == 1
    assert "brand new lesson" in adds[0].payload["content"]
    # One POST was made to the mock API.
    assert len(state.proposed_posts) == 1
    assert state.proposed_posts[0]["change_type"] == "memory_add"


def test_capture_detects_new_skill_dir(seeded_state, persona_repo, tmp_path):
    state, base = seeded_state
    out = _render_then_baseline(seeded_state, persona_repo, tmp_path)

    # Agent creates a new skill dir.
    new_skill = out / "skills" / "new-thing"
    new_skill.mkdir(parents=True)
    (new_skill / "SKILL.md").write_text("# new-thing\nA discovered capability.\n")

    state.proposed_posts.clear()
    cfg = CaptureConfig(
        persona_id=1,
        scheduler_run_id=999,
        post_run_dir=out,
        platform_api_base=base,
    )
    result = capture_persona_mutations(cfg)
    skill_adds = [c for c in result.proposed_changes if c.change_type == "skill_add"]
    assert len(skill_adds) == 1
    assert skill_adds[0].payload["skill_key"] == "new-thing"
    assert any(p["change_type"] == "skill_add" for p in state.proposed_posts)


def test_capture_dry_run_does_not_post(seeded_state, persona_repo, tmp_path):
    state, base = seeded_state
    out = _render_then_baseline(seeded_state, persona_repo, tmp_path)
    (out / "skills" / "dry-skill").mkdir()
    (out / "skills" / "dry-skill" / "SKILL.md").write_text("# dry-skill\n")

    state.proposed_posts.clear()
    cfg = CaptureConfig(
        persona_id=1,
        scheduler_run_id=999,
        post_run_dir=out,
        platform_api_base=base,
        dry_run=True,
    )
    result = capture_persona_mutations(cfg)
    assert any(c.change_type == "skill_add" for c in result.proposed_changes)
    assert state.proposed_posts == []
    assert result.posted_ids == []


def test_capture_excludes_hermes_managed_dirs(seeded_state, persona_repo, tmp_path):
    state, base = seeded_state
    out = _render_then_baseline(seeded_state, persona_repo, tmp_path)

    # Simulate Hermes runtime artefacts present in the snapshot.
    (out / "sessions").mkdir()
    (out / "sessions" / "run.log").write_text("noisy log\n")
    (out / "logs").mkdir()
    (out / "logs" / "gateway.log").write_text("noisy log\n")
    (out / "state.db").write_text("sqlite\n")
    (out / "gateway.pid").write_text("12345\n")
    (out / "memories").mkdir()
    (out / "memories" / "hermes-internal.json").write_text("{}")

    state.proposed_posts.clear()
    cfg = CaptureConfig(
        persona_id=1,
        scheduler_run_id=999,
        post_run_dir=out,
        platform_api_base=base,
        dry_run=True,
    )
    result = capture_persona_mutations(cfg)
    # No skill/memory adds from these files.
    assert all(c.change_type not in ("skill_add", "skill_update") for c in result.proposed_changes)
    # All those Hermes paths were skipped.
    skipped = set(result.skipped_hermes_managed)
    assert "sessions/run.log" in skipped
    assert "logs/gateway.log" in skipped
    assert "state.db" in skipped
    assert "gateway.pid" in skipped
    assert "memories/hermes-internal.json" in skipped


def test_capture_handles_unchanged_state(seeded_state, persona_repo, tmp_path):
    state, base = seeded_state
    out = _render_then_baseline(seeded_state, persona_repo, tmp_path)

    state.proposed_posts.clear()
    cfg = CaptureConfig(
        persona_id=1,
        scheduler_run_id=999,
        post_run_dir=out,
        platform_api_base=base,
    )
    result = capture_persona_mutations(cfg)
    # MEMORY.md untouched → the rendered entries all have ids, so no memory_add.
    assert all(c.change_type != "memory_add" for c in result.proposed_changes)
    assert state.proposed_posts == []
