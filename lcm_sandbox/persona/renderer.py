# File: lcm_sandbox/persona/renderer.py
# Description: Persona state renderer. Fetches a persona's row, persona-scoped typed_memory,
#              and persona-instance skill_registry rows from the AIDevOps platform REST API,
#              then materializes the persona-owned file set into a host directory that the
#              container launch layer will `docker cp` into the sandbox's HERMES_HOME.
#              Implements ACTIVITY START half of WP-8 mutation flow.
# Author: Libor Ballaty <libor@arionetworks.com>
# Created: 2026-06-12

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

# Files that the persona OWNS (renderer writes them; capturer diffs them).
# Everything else under HERMES_HOME is Hermes-managed runtime state — see
# verification/phase-0-hermes-container/REPORT.md Finding 3.
PERSONA_OWNED_TOP_LEVEL = {
    "config.yaml",
    "SOUL.md",
    "MEMORY.md",
    "USER.md",
    ".env",
    "mcp-servers.yaml",
    "allowlists.yaml",
    "persona.json",
}
PERSONA_OWNED_DIRS = {"skills"}

SUBSTITUTION_KEYS = ("MODEL_PROVIDER", "MODEL_KEY", "MCP_SERVER_URL", "MCP_TOKEN")


@dataclass
class RenderConfig:
    persona_key: str
    output_dir: Path
    persona_repo_path: Path
    platform_api_base: str = "http://localhost:9700"
    platform_api_key: str = "dev-key"
    mcp_token: str = ""
    mcp_url: str = ""
    model_provider: str = ""
    model_key: str = ""
    # Optional override: if the caller already knows the persona id, skip the lookup.
    persona_id: int | None = None


@dataclass
class RenderResult:
    persona_id: int
    persona_key: str
    output_dir: Path
    rendered_files: list[str] = field(default_factory=list)
    baseline_hash_file: Path | None = None


class RenderError(RuntimeError):
    def __init__(self, message: str, *, step: str = "", context: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.step = step
        self.context = context or {}

    def to_json(self) -> dict[str, Any]:
        return {
            "status": "error",
            "error_type": "RenderError",
            "step": self.step,
            "message": self.message,
            "context": self.context,
        }


# ── HTTP helpers ──────────────────────────────────────────────────────────────


def _http_get(url: str, api_key: str, timeout: float = 10.0) -> Any:
    req = urllib_request.Request(
        url,
        headers={
            "X-AIDevOps-Key": api_key,
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib_request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except urllib_error.HTTPError as e:
        raise RenderError(
            f"HTTP {e.code} from {url}",
            step="http_get",
            context={"url": url, "status": e.code, "body": e.read().decode("utf-8", "replace")[:500]},
        ) from e
    except urllib_error.URLError as e:
        raise RenderError(
            f"Network error reaching {url}: {e.reason}",
            step="http_get",
            context={"url": url},
        ) from e
    except json.JSONDecodeError as e:
        raise RenderError(
            f"Non-JSON response from {url}",
            step="http_get",
            context={"url": url, "snippet": str(e)[:200]},
        ) from e


# ── Substitution ──────────────────────────────────────────────────────────────


def _apply_substitutions(text: str, values: dict[str, str]) -> str:
    out = text
    for key in SUBSTITUTION_KEYS:
        placeholder = "${" + key + "}"
        out = out.replace(placeholder, values.get(key, ""))
    return out


# ── MEMORY.md assembly ────────────────────────────────────────────────────────


def _assemble_memory_md(persona_key: str, memory_rows: list[dict[str, Any]]) -> str:
    """Group active typed_memory rows by memory_type and emit a deterministic markdown doc.

    Format:
        # Memory — <persona_key>

        ## Lessons
        - <content>

        ## Decisions
        - <content>
        ...
    """
    by_type: dict[str, list[dict[str, Any]]] = {}
    for row in memory_rows:
        if row.get("state") and row["state"] != "active":
            continue
        mt = row.get("memory_type") or "note"
        by_type.setdefault(mt, []).append(row)

    lines: list[str] = [f"# Memory — {persona_key}", ""]
    if not by_type:
        lines.append("_(empty — no persona-scoped typed_memory rows)_")
        lines.append("")
        return "\n".join(lines)

    # Stable ordering: lessons, decisions, then alphabetical.
    preferred = ["lesson", "decision"]
    ordered_types = [t for t in preferred if t in by_type] + sorted(
        t for t in by_type if t not in preferred
    )

    for mt in ordered_types:
        heading = mt.replace("_", " ").title() + "s" if not mt.endswith("s") else mt.title()
        lines.append(f"## {heading}")
        lines.append("")
        for row in by_type[mt]:
            content = (row.get("content") or "").strip()
            mem_id = row.get("id")
            if not content:
                continue
            # Encode id so capturer can map back to the source row when proposing updates/deletes.
            lines.append(f"- <!-- id:{mem_id} --> {content}")
        lines.append("")
    return "\n".join(lines)


# ── Hash helpers ──────────────────────────────────────────────────────────────


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _compute_baseline_hashes(output_dir: Path) -> dict[str, str]:
    """Hash every persona-owned file under output_dir. Keys are relative POSIX paths."""
    hashes: dict[str, str] = {}
    for p in sorted(output_dir.rglob("*")):
        if p.is_dir():
            continue
        rel = p.relative_to(output_dir).as_posix()
        if rel == ".baseline-hashes.json":
            continue
        hashes[rel] = _sha256_file(p)
    return hashes


# ── Main entry ────────────────────────────────────────────────────────────────


def render_persona(cfg: RenderConfig) -> RenderResult:
    output_dir = Path(cfg.output_dir)
    persona_repo_path = Path(cfg.persona_repo_path)

    if not persona_repo_path.is_dir():
        raise RenderError(
            f"persona_repo_path does not exist: {persona_repo_path}",
            step="validate_inputs",
        )

    # 1. Resolve persona row.
    persona = _resolve_persona(cfg)
    persona_id = persona["id"]
    persona_dir_path = persona.get("persona_dir_path") or f"personas/{cfg.persona_key}"
    persona_dir = persona_repo_path / persona_dir_path
    if not persona_dir.is_dir():
        raise RenderError(
            f"Persona directory not found on disk: {persona_dir}",
            step="locate_persona_dir",
            context={"persona_repo_path": str(persona_repo_path), "persona_dir_path": persona_dir_path},
        )

    # 2. Fetch memory + skills.
    memory_resp = _http_get(
        f"{cfg.platform_api_base.rstrip('/')}/api/personas/{persona_id}/memory",
        cfg.platform_api_key,
    )
    memory_rows = _extract_data(memory_resp)

    skills_resp = _http_get(
        f"{cfg.platform_api_base.rstrip('/')}/api/personas/{persona_id}/skills",
        cfg.platform_api_key,
    )
    all_skill_rows = _extract_data(skills_resp)
    instance_skills = [s for s in all_skill_rows if s.get("asset_layer") == "persona_instance"]

    # 3. Reset output dir (idempotent render).
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "skills").mkdir(parents=True, exist_ok=True)

    rendered: list[str] = []
    sub_values = {
        "MODEL_PROVIDER": cfg.model_provider,
        "MODEL_KEY": cfg.model_key,
        "MCP_SERVER_URL": cfg.mcp_url,
        "MCP_TOKEN": cfg.mcp_token,
    }

    # 4. Verbatim copies: SOUL.md, persona.json
    for fname in ("SOUL.md", "persona.json"):
        src = persona_dir / fname
        if src.is_file():
            shutil.copy2(src, output_dir / fname)
            rendered.append(fname)

    # 5. Substituted copies: config.yaml, mcp-servers.yaml, allowlists.yaml
    for fname in ("config.yaml", "mcp-servers.yaml", "allowlists.yaml"):
        src = persona_dir / fname
        if src.is_file():
            text = src.read_text(encoding="utf-8")
            (output_dir / fname).write_text(_apply_substitutions(text, sub_values), encoding="utf-8")
            rendered.append(fname)

    # 6. MEMORY.md from DB.
    memory_md = _assemble_memory_md(cfg.persona_key, memory_rows)
    (output_dir / "MEMORY.md").write_text(memory_md, encoding="utf-8")
    rendered.append("MEMORY.md")

    # 7. .env with API_SERVER_KEY (Phase 0 Finding 2 + 4 — same token both directions).
    env_lines = [
        f"# File: .env (rendered by persona-state-renderer at {datetime.now(timezone.utc).isoformat()})",
        f"# Persona: {cfg.persona_key} (id={persona_id})",
        f"API_SERVER_KEY={cfg.mcp_token}" if cfg.mcp_token else "# API_SERVER_KEY not set (no --mcp-token provided)",
        "",
    ]
    (output_dir / ".env").write_text("\n".join(env_lines), encoding="utf-8")
    rendered.append(".env")

    # 8. Skills: persona-instance rows from DB + any files present on disk.
    skills_dir = output_dir / "skills"
    for skill in instance_skills:
        skill_key = skill.get("skill_key")
        if not skill_key:
            continue
        sk_target = skills_dir / skill_key
        sk_target.mkdir(parents=True, exist_ok=True)
        # If the persona repo has a corresponding skill dir, copy contents (with substitutions on yaml/md).
        repo_skill_dir = persona_dir / "skills" / skill_key
        if repo_skill_dir.is_dir():
            for entry in repo_skill_dir.rglob("*"):
                if entry.is_dir():
                    continue
                rel = entry.relative_to(repo_skill_dir)
                dest = sk_target / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                if entry.suffix in (".yaml", ".yml", ".md"):
                    dest.write_text(
                        _apply_substitutions(entry.read_text(encoding="utf-8"), sub_values),
                        encoding="utf-8",
                    )
                else:
                    shutil.copy2(entry, dest)
        # Always (re)write SKILL.md from registry metadata so it reflects DB truth.
        skill_md = _assemble_skill_md(skill)
        (sk_target / "SKILL.md").write_text(skill_md, encoding="utf-8")
        rendered.append(f"skills/{skill_key}/SKILL.md")

    # Also copy any disk-only skill dirs (template skills the persona keeps in its repo but
    # not yet promoted to a persona-instance row) — useful for first-time renders.
    disk_skills_dir = persona_dir / "skills"
    if disk_skills_dir.is_dir():
        existing = {s.get("skill_key") for s in instance_skills}
        for sub in disk_skills_dir.iterdir():
            if not sub.is_dir() or sub.name in existing:
                continue
            sk_target = skills_dir / sub.name
            sk_target.mkdir(parents=True, exist_ok=True)
            for entry in sub.rglob("*"):
                if entry.is_dir():
                    continue
                rel = entry.relative_to(sub)
                dest = sk_target / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                if entry.suffix in (".yaml", ".yml", ".md"):
                    dest.write_text(
                        _apply_substitutions(entry.read_text(encoding="utf-8"), sub_values),
                        encoding="utf-8",
                    )
                else:
                    shutil.copy2(entry, dest)
                rendered.append(f"skills/{sub.name}/{rel.as_posix()}")

    # 9. Baseline hashes.
    hashes = _compute_baseline_hashes(output_dir)
    baseline_path = output_dir / ".baseline-hashes.json"
    baseline_path.write_text(
        json.dumps(
            {
                "persona_id": persona_id,
                "persona_key": cfg.persona_key,
                "rendered_at": datetime.now(timezone.utc).isoformat(),
                "hashes": hashes,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    return RenderResult(
        persona_id=persona_id,
        persona_key=cfg.persona_key,
        output_dir=output_dir,
        rendered_files=sorted(rendered),
        baseline_hash_file=baseline_path,
    )


def _resolve_persona(cfg: RenderConfig) -> dict[str, Any]:
    """Look up persona by id (if provided) or by persona_key via the list endpoint."""
    if cfg.persona_id is not None:
        resp = _http_get(
            f"{cfg.platform_api_base.rstrip('/')}/api/personas/{cfg.persona_id}",
            cfg.platform_api_key,
        )
        data = _extract_data(resp)
        if not data:
            raise RenderError(
                f"Persona id={cfg.persona_id} not found",
                step="resolve_persona",
            )
        return data if isinstance(data, dict) else data[0]

    resp = _http_get(
        f"{cfg.platform_api_base.rstrip('/')}/api/personas",
        cfg.platform_api_key,
    )
    rows = _extract_data(resp)
    if not isinstance(rows, list):
        raise RenderError(
            "Unexpected personas list shape (not an array)",
            step="resolve_persona",
            context={"resp_type": type(rows).__name__},
        )
    for row in rows:
        if row.get("persona_key") == cfg.persona_key:
            return row
    raise RenderError(
        f"persona_key not found: {cfg.persona_key}",
        step="resolve_persona",
        context={"available": [r.get("persona_key") for r in rows]},
    )


def _extract_data(resp: Any) -> Any:
    """The API wraps payloads as {ok|data: ...}. Normalize."""
    if isinstance(resp, dict):
        if "data" in resp:
            return resp["data"]
        if "rows" in resp:
            return resp["rows"]
    return resp


def _assemble_skill_md(skill: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"# {skill.get('display_name') or skill.get('skill_key')}",
            "",
            f"**skill_key:** `{skill.get('skill_key')}`",
            f"**asset_layer:** {skill.get('asset_layer')}",
            f"**status:** {skill.get('status')}",
            f"**instantiated_from_skill_id:** {skill.get('instantiated_from_skill_id')}",
            "",
            "## Description",
            "",
            (skill.get("description") or "_(no description)_").strip(),
            "",
        ]
    )


# Persona-owned filter used by both renderer and capturer.
def is_persona_owned(rel_path: str) -> bool:
    """Return True if a relative path inside HERMES_HOME is persona-owned (renderer writes / capturer diffs)."""
    parts = Path(rel_path).parts
    if not parts:
        return False
    top = parts[0]
    if top in PERSONA_OWNED_DIRS:
        return True
    if len(parts) == 1 and top in PERSONA_OWNED_TOP_LEVEL:
        return True
    return False


# Explicit Hermes-managed exclusion list (negative filter, used as a safety net).
HERMES_MANAGED_TOP_LEVEL_DIRS = {
    "hermes-agent",
    "node",
    "bin",
    "sessions",
    "logs",
    "cron",
    "memories",  # Hermes' own memory dir, NOT MEMORY.md
    "audio_cache",
    "image_cache",
    "hooks",
    "pairing",
    "sandboxes",  # Hermes' internal sandbox dir
}
HERMES_MANAGED_TOP_LEVEL_FILES = {
    "state.db",
    "state.db-shm",
    "state.db-wal",
    "response_store.db",
    "response_store.db-shm",
    "response_store.db-wal",
    "kanban.db",
    "kanban.db-shm",
    "kanban.db-wal",
    "gateway.pid",
    "gateway_state.json",
    "channel_directory.json",
    ".install_method",
    ".update_check",
}


def is_hermes_managed(rel_path: str) -> bool:
    parts = Path(rel_path).parts
    if not parts:
        return False
    top = parts[0]
    if top in HERMES_MANAGED_TOP_LEVEL_DIRS:
        return True
    if len(parts) == 1:
        if top in HERMES_MANAGED_TOP_LEVEL_FILES:
            return True
        if top.endswith(".lock"):
            return True
    return False


__all__ = [
    "RenderConfig",
    "RenderResult",
    "RenderError",
    "render_persona",
    "is_persona_owned",
    "is_hermes_managed",
    "PERSONA_OWNED_TOP_LEVEL",
    "PERSONA_OWNED_DIRS",
    "HERMES_MANAGED_TOP_LEVEL_DIRS",
    "HERMES_MANAGED_TOP_LEVEL_FILES",
]
