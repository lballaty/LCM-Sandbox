# HERMES Persona Integration — Pointer

**File:** `HERMES-PERSONA-INTEGRATION-PLAN.md`
**Description:** Pointer / shim document for the canonical HERMES Persona Integration Plan, which lives in a sibling repository. The LCM-Sandbox-side implementation (WP-8) references this doc; this file exists so a reader who finds the reference in `lcm_sandbox/persona/__init__.py` can resolve it without spelunking through other repos.
**Author:** Libor Ballaty <libor@arionetworks.com>
**Created:** 2026-06-17
**Last Updated:** 2026-06-17
**Last Updated By:** Libor Ballaty

---

> **External canonical source:** `/Users/liborballaty/LocalProjects/GitHubProjectsDocuments/aidevops/design/HERMES-PERSONA-INTEGRATION-PLAN.md` (793 lines, in the `aidevops` repo). This is the authoritative document for the HERMES persona feature — design rationale, work-package decomposition (WP-0 through WP-N), the AIDevOps-side API surface (`/api/personas`, `proposed_persona_changes`), and the persona-owned file set (`config.yaml`, `SOUL.md`, `MEMORY.md`, `.env`, `skills/`).

## Why the doc lives there

Persona orchestration is owned by the AIDevOps platform: the platform exposes `/api/personas`, stores `proposed_persona_changes`, decides which persona key to run, and supplies the bearer tokens that the sandbox uses to read its initial state. LCM-Sandbox is the *consumer* of that contract — `lcm_sandbox/persona/renderer.py` calls into the platform API; `lcm_sandbox/persona/capturer.py` writes change events back. Keeping the contract spec next to its server-side owner avoids the canonical-doc drift that comes from cross-repo duplication.

## What lives in this repo (LCM-Sandbox)

| Concern | Path |
| :--- | :--- |
| Renderer — materialises persona-owned files at sandbox start | `lcm_sandbox/persona/renderer.py` |
| Capturer — diffs the post-run state and emits `proposed_persona_changes` events | `lcm_sandbox/persona/capturer.py` |
| Console scripts (`persona-state-renderer`, `persona-state-capturer`) | `lcm_sandbox/persona/cli.py` |
| Unit tests | `lcm_sandbox/tests/test_persona_render_capture.py` |
| In-container wiring (entrypoint invokes the renderer when `HERMES_PERSONA` is set) | `scripts/entrypoint.sh` step 4.5.10 |
| Phase 0 verification report (referenced by `Dockerfile.hermes`) | `aidevops/design/verification/phase-0-hermes-container/REPORT.md` |

## What lives in the AIDevOps repo

| Concern | Path (relative to `aidevops/`) |
| :--- | :--- |
| Integration plan (canonical) | `design/HERMES-PERSONA-INTEGRATION-PLAN.md` |
| Progress tracker | `design/HERMES-PERSONA-PROGRESS.md` |
| Platform API exposing `/api/personas` and accepting `proposed_persona_changes` writes | server modules under `server/modules/` |
| Phase 0 container verification report | `design/verification/phase-0-hermes-container/REPORT.md` |
| Persona repo (separate sibling: `aidevops-hermes-personas/`) | `/Users/liborballaty/LocalProjects/GitHubProjectsDocuments/aidevops-hermes-personas/` |

## When this pointer needs updating

- The canonical doc moves to a different repo or path.
- The work-package nomenclature changes (e.g. WP-8 is renumbered).
- The contract between LCM-Sandbox and AIDevOps changes shape (new endpoint, new field on `proposed_persona_changes`, etc.) — surface that in `lcm_sandbox/persona/renderer.py` first, then update this pointer to match.

If you find yourself wanting to copy content from the canonical doc into here, don't — link instead. Sandboxes consume the persona contract; they don't define it.
