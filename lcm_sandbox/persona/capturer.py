# File: lcm_sandbox/persona/capturer.py
# Description: Persona state capturer. Reads the post-run host directory (populated by
#              `docker cp` from the sandbox HERMES_HOME), diffs persona-owned files against
#              the baseline hashes written by the renderer, and POSTs proposed_persona_changes
#              entries to the AIDevOps platform REST API.
#              Implements ACTIVITY END half of WP-8 mutation flow.
# Author: Libor Ballaty <libor@arionetworks.com>
# Created: 2026-06-12

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from lcm_sandbox.persona.renderer import (
    _sha256_file,
    _compute_baseline_hashes,
    is_hermes_managed,
    is_persona_owned,
)


@dataclass
class CaptureConfig:
    persona_id: int
    scheduler_run_id: int
    post_run_dir: Path
    baseline_hash_file: Path | None = None  # defaults to post_run_dir / .baseline-hashes.json
    platform_api_base: str = "http://localhost:9700"
    platform_api_key: str = "dev-key"
    dry_run: bool = False


@dataclass
class ProposedChange:
    change_type: str
    payload: dict[str, Any]
    baseline_hash: str | None = None


@dataclass
class CaptureResult:
    persona_id: int
    scheduler_run_id: int
    proposed_changes: list[ProposedChange] = field(default_factory=list)
    posted_ids: list[int] = field(default_factory=list)
    skipped_hermes_managed: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class CaptureError(RuntimeError):
    def __init__(self, message: str, *, step: str = "", context: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.step = step
        self.context = context or {}

    def to_json(self) -> dict[str, Any]:
        return {
            "status": "error",
            "error_type": "CaptureError",
            "step": self.step,
            "message": self.message,
            "context": self.context,
        }


# ── MEMORY.md parsing ─────────────────────────────────────────────────────────

_MEM_LINE_RE = re.compile(r"^\-\s+(?:<!--\s*id:(?P<id>\d+)\s*-->\s*)?(?P<content>.+)$")
_HEADING_RE = re.compile(r"^##\s+(?P<heading>.+?)\s*$")


def _parse_memory_md(text: str) -> list[dict[str, Any]]:
    """Return a flat list of memory entries from MEMORY.md.

    Each entry: { memory_type: str, content: str, id: int|None }
    """
    entries: list[dict[str, Any]] = []
    current_type: str | None = None
    for line in text.splitlines():
        m_head = _HEADING_RE.match(line)
        if m_head:
            heading = m_head.group("heading").strip().lower()
            # "Lessons" -> "lesson", "Decisions" -> "decision"
            current_type = heading.rstrip("s") if heading.endswith("s") else heading
            continue
        m_line = _MEM_LINE_RE.match(line.strip())
        if m_line and current_type:
            entries.append(
                {
                    "memory_type": current_type,
                    "content": m_line.group("content").strip(),
                    "id": int(m_line.group("id")) if m_line.group("id") else None,
                }
            )
    return entries


# ── HTTP ──────────────────────────────────────────────────────────────────────


def _http_post(url: str, api_key: str, body: dict[str, Any], timeout: float = 10.0) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req = urllib_request.Request(
        url,
        data=data,
        headers={
            "X-AIDevOps-Key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=timeout) as resp:
            body_bytes = resp.read()
            if not body_bytes:
                return {}
            return json.loads(body_bytes.decode("utf-8"))
    except urllib_error.HTTPError as e:
        raise CaptureError(
            f"HTTP {e.code} from {url}",
            step="http_post",
            context={"url": url, "status": e.code, "body": e.read().decode("utf-8", "replace")[:500]},
        ) from e
    except urllib_error.URLError as e:
        raise CaptureError(
            f"Network error reaching {url}: {e.reason}",
            step="http_post",
            context={"url": url},
        ) from e


# ── Main ──────────────────────────────────────────────────────────────────────


def capture_persona_mutations(cfg: CaptureConfig) -> CaptureResult:
    post_run_dir = Path(cfg.post_run_dir)
    if not post_run_dir.is_dir():
        raise CaptureError(
            f"post_run_dir does not exist: {post_run_dir}",
            step="validate_inputs",
        )

    baseline_path = Path(cfg.baseline_hash_file or (post_run_dir / ".baseline-hashes.json"))
    if not baseline_path.is_file():
        raise CaptureError(
            f"baseline hash file not found: {baseline_path}",
            step="load_baseline",
        )
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline_hashes: dict[str, str] = baseline.get("hashes", {})

    result = CaptureResult(persona_id=cfg.persona_id, scheduler_run_id=cfg.scheduler_run_id)

    # 1. Snapshot current state (persona-owned only).
    current: dict[str, str] = {}
    for p in sorted(post_run_dir.rglob("*")):
        if p.is_dir():
            continue
        rel = p.relative_to(post_run_dir).as_posix()
        if rel == ".baseline-hashes.json":
            continue
        if is_hermes_managed(rel):
            result.skipped_hermes_managed.append(rel)
            continue
        if not is_persona_owned(rel):
            # Out-of-scope file (e.g. Hermes wrote something unexpected). Skip but note.
            result.skipped_hermes_managed.append(rel)
            continue
        current[rel] = _sha256_file(p)

    baseline_keys = set(baseline_hashes.keys())
    current_keys = set(current.keys())

    added = current_keys - baseline_keys
    deleted = baseline_keys - current_keys
    common = baseline_keys & current_keys
    changed = {k for k in common if baseline_hashes[k] != current[k]}

    # 2. MEMORY.md → memory_add / memory_update / memory_delete.
    if "MEMORY.md" in changed or "MEMORY.md" in added:
        new_text = (post_run_dir / "MEMORY.md").read_text(encoding="utf-8") if (post_run_dir / "MEMORY.md").is_file() else ""
        new_entries = _parse_memory_md(new_text)

        # Reconstruct old entries from the original rendered MEMORY.md if it's preserved in
        # the baseline tree alongside the hash file (we don't keep the file, so we'll do a
        # content-based diff: any entry in new without an id is an add; any id present in new
        # is treated as unchanged unless its content differs (which we can't detect without
        # the old content). For Phase 1 simplicity we treat:
        #   - entries with id=None  → memory_add
        #   - entries with id set   → ignored (treated as unchanged; deletion not detected
        #                              without snapshotting baseline content)
        # Deletions can be approximated only if we snapshot baseline content. TODO documented.
        for entry in new_entries:
            if entry["id"] is None:
                result.proposed_changes.append(
                    ProposedChange(
                        change_type="memory_add",
                        payload={
                            "memory_type": entry["memory_type"],
                            "content": entry["content"],
                            "source": f"persona-capturer:run={cfg.scheduler_run_id}",
                        },
                        baseline_hash=baseline_hashes.get("MEMORY.md"),
                    )
                )
        result.notes.append(
            "MEMORY.md diff used add-only heuristic; update/delete require baseline content snapshot (TODO)"
        )

    # 3. Skills: dir-level diff.
    baseline_skills = _group_skill_files(baseline_keys)
    current_skills = _group_skill_files(current_keys)

    added_skills = set(current_skills.keys()) - set(baseline_skills.keys())
    deleted_skills = set(baseline_skills.keys()) - set(current_skills.keys())
    common_skills = set(current_skills.keys()) & set(baseline_skills.keys())

    for skill_key in sorted(added_skills):
        skill_md_rel = f"skills/{skill_key}/SKILL.md"
        skill_md_path = post_run_dir / skill_md_rel
        description = ""
        if skill_md_path.is_file():
            description = skill_md_path.read_text(encoding="utf-8")
        result.proposed_changes.append(
            ProposedChange(
                change_type="skill_add",
                payload={
                    "skill_key": skill_key,
                    "display_name": skill_key,
                    "description": description,
                },
            )
        )

    for skill_key in sorted(deleted_skills):
        result.proposed_changes.append(
            ProposedChange(
                change_type="skill_delete",
                payload={"skill_key": skill_key},
            )
        )

    for skill_key in sorted(common_skills):
        # Any file inside this skill dir changed?
        skill_changed = any(
            (f in changed) for f in current_skills[skill_key]
        )
        if skill_changed:
            skill_md_rel = f"skills/{skill_key}/SKILL.md"
            description = ""
            if (post_run_dir / skill_md_rel).is_file():
                description = (post_run_dir / skill_md_rel).read_text(encoding="utf-8")
            result.proposed_changes.append(
                ProposedChange(
                    change_type="skill_update",
                    payload={
                        "skill_key": skill_key,
                        "description": description,
                    },
                    baseline_hash=baseline_hashes.get(skill_md_rel),
                )
            )

    # 4. config.yaml / .env / mcp-servers.yaml / allowlists.yaml — infrastructure, not learning.
    #    These are notes, not proposed_persona_changes (per spec).
    for infra in ("config.yaml", ".env", "mcp-servers.yaml", "allowlists.yaml", "SOUL.md", "USER.md", "persona.json"):
        if infra in changed or infra in added or infra in deleted:
            result.notes.append(f"infrastructure file changed (not promoted): {infra}")

    # 5. POST proposed changes (unless dry-run).
    if not cfg.dry_run:
        for change in result.proposed_changes:
            resp = _http_post(
                f"{cfg.platform_api_base.rstrip('/')}/api/personas/proposed-changes",
                cfg.platform_api_key,
                {
                    "persona_id": cfg.persona_id,
                    "scheduler_run_id": cfg.scheduler_run_id,
                    "change_type": change.change_type,
                    "payload": change.payload,
                    "baseline_hash": change.baseline_hash,
                },
            )
            data = resp.get("data") if isinstance(resp, dict) else None
            if isinstance(data, dict) and "id" in data:
                result.posted_ids.append(int(data["id"]))

    return result


def _group_skill_files(rel_paths: set[str]) -> dict[str, list[str]]:
    """Group skills/<skill_key>/* files by skill_key."""
    out: dict[str, list[str]] = {}
    for rel in rel_paths:
        parts = Path(rel).parts
        if len(parts) >= 2 and parts[0] == "skills":
            out.setdefault(parts[1], []).append(rel)
    return out


__all__ = [
    "CaptureConfig",
    "CaptureResult",
    "ProposedChange",
    "CaptureError",
    "capture_persona_mutations",
]
