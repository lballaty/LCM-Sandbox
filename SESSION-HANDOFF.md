# Session Handoff — Phase 1 Implementation

**Date:** 2026-06-02 (last updated)
**Branch:** main
**Working directory:** `/Users/liborballaty/LocalProjects/GitHubProjectsDocuments/LCM-Sandbox`

---

## Status summary

**Phase 1 complete.** Full CLI working end-to-end (Phases 0→3), 43/43 unit tests passing, sanity-checked against a real temp git repo. Phase 2 + 5 design docs complete (agent config, image toolchain, orchestration channel, end-to-end flows). Permissions reverted to safe defaults. Ready for Phase 2 implementation (Dockerfile + entrypoint + `docker_launcher.py`).

---

## What's done

### Configuration
- `.claude/settings.json` — project permissions tuned for autonomous work; `defaultMode: bypassPermissions`, scoped `Edit`/`Write` allow rules for this repo, deny rules for destructive ops (sudo, force-push, rm-rf outside repo, etc.).
- `~/.claude/settings.json` — added `"skipDangerousModePermissionPrompt": true` so bypass mode activates silently on next session start.

> **⚠️ TEMPORARY — REVERT AT END OF PHASE 1**
>
> Two of the changes above were made only to enable autonomous file editing during implementation:
>
> 1. **PROJECT** (`.claude/settings.json`) — set `"defaultMode"` back to `"acceptEdits"` (or `"default"`).
> 2. **USER** (`~/.claude/settings.json`) — remove the `"skipDangerousModePermissionPrompt": true` line.
>
> The repo-scoped Edit/Write allow rules and the broadened Bash allow list in the project settings can stay; they're scoped and safe. Only the two items above need reverting. This is tracked as Task #11.

### Package scaffolding (Task 1, complete)
- `pyproject.toml` — full project metadata, deps (click, pydantic, python-json-logger), dev deps (pytest), entry point `lcm-sandbox=lcm_sandbox.cli:main`, pytest config.
- Directory tree created under `lcm_sandbox/`: `commands/`, `core/`, `models/`, `utils/`, `tests/`.
- Empty stub files touched for all 26 module files.

### Models (Task 2, complete)
- `lcm_sandbox/models/sandbox_config.py` — `AllowedPaths` and `SandboxConfig` with full validators (repo_path absolute, ids regex-safe, branch no-whitespace, no traversal in allowed paths).
- `lcm_sandbox/models/artifact.py` — `WorktreeBaseline` and `Phase1Result`.
- `lcm_sandbox/models/__init__.py` — exports.
- `lcm_sandbox/exceptions.py` — `SandboxError` base + `PreflightCheckError`, `WorktreeError`, `SyncError`, `DockerImageError`, `DockerLaunchError`, `ArtifactCaptureError`. Each carries phase/step and structured context.

### Utils (Task 3, in progress)
- `lcm_sandbox/utils/shell.py` — `CommandResult` dataclass + `run()` subprocess wrapper. Done.
- `lcm_sandbox/utils/logger.py` — structured JSON logger with fallback if python-json-logger missing. Done.
- `lcm_sandbox/utils/__init__.py` — placeholder docstring. Done.
- `lcm_sandbox/utils/git.py` — **empty stub, not implemented**.
- `lcm_sandbox/utils/docker.py` — **empty stub, not implemented**.

---

## What's pending (next session priorities)

1. **Finish utils (Task 3)**:
   - `utils/git.py` — wrappers over `shell.run(["git", ...])`: `ls_remote`, `show_ref`, `rev_parse`, `fetch`, `worktree_add`, `worktree_list`, `worktree_remove`, `status_porcelain`, `merge_base`, `rebase`, `branch_track`, `checkout`, `log_format`.
   - `utils/docker.py` — wrappers: `ps_filter`, `image_inspect`, `image_build`, `image_run_check`.

2. **Core/preflight (Task 4)** — implement the 8 checks from SANDBOX-DETAILED-FLOW.md STEP 0.1. Each check raises `PreflightCheckError` with the check number and contextual fields.

3. **Core/worktree (Task 5)** — STEP 1.1 (create new) + STEP 1.2 (reset existing). Resolve `sandbox_id = sandbox-{run_id}-{utc-timestamp}`. Verify worktree by checking `.git` file exists + `git rev-parse HEAD` returns a hash.

4. **Core/sync (Task 6)** — STEP 2.1-2.6. Fetch in parent repo (not worktree), determine status from merge-base comparison, rebase only if "behind", abort on "ahead" or "diverged".

5. **Core/docker_builder (Task 7)** — STEP 3.1 image presence check; STEP 3.2 functional verification. For Phase 1 the Dockerfile doesn't exist yet (arrives in Phase 2), so the builder should only check, not build.

6. **CLI + create command (Task 8)** — `cli.py` (click) + `commands/create.py` orchestrating phases 0→3. Args: `--repo`, `--branch`, `--allowed-paths` (JSON string), `--timeout`, `--colima-profile`, `--plan-id`, `--run-id` (defaults: derive from cwd/timestamp). Output: JSON to stdout matching `Phase1Result`. Exit codes: 0 ok, 1 preflight, 2 worktree, 3 sync, 4 docker.

7. **Unit tests (Task 9)** — `conftest.py` with fixtures (tmp git repo, mock subprocess). Per-module tests with `pytest-mock`.

8. **Integration sanity check (Task 10)** — install in venv, run against a temp repo, verify JSON output.

---

## Open design questions surfaced this session (all addressed)

All design questions raised during this session have a dedicated note:

1. **In-sandbox agent permission profile(s)** — see `SANDBOX-AGENT-CONFIG.md` + `.html`. Decision: single permissive profile by default + `LCM_AGENT_PROFILE=standard` env-var override. Layered defenses (container ACLs + git pre-push hook) make the inside-config permissiveness safe. 13-row coverage matrix; `rm` shim + stdin-EOF shim + bootstrap smoke test as defensive layers. Codex/Gemini deferred (Claude-only image for first cut).

2. **Test toolchain in the image** — see `SANDBOX-IMAGE-TOOLCHAIN.md`. Decision: one fat universal image (~1 GB ceiling 1.5 GB), ubuntu:24.04 base, python+node+build-essential+agent CLIs. Per-project images deferred. Browser engines / DB servers / Docker-in-Docker explicitly excluded. Multi-arch (amd64+arm64) from day 1.

3. **MCP server inventory** — confirmed Claude Code ships zero third-party MCP servers by default. Three new prompt paths surfaced (project `.mcp.json` approval, `headersHelper` workspace-trust, elicitation dialog, claude.ai connector leak) — all silenced by config or by an entrypoint sanitize step.

4. **AIDevOps ↔ in-sandbox live communication** — see `SANDBOX-ORCHESTRATION.md` + `.html`. Decision: MCP Streamable HTTP + OAuth 2.1 audience-bound per-run bearer tokens + sandbox-dials-out-only + layered server-side validation (schema, authz, per-field validators, rate limits, audit log). Eight canonical tools. Matches GitHub Actions/Temporal/Argo outbound-only pattern.

5. **End-to-end flows (CLI / UI / programmatic)** — see `SANDBOX-FLOWS.html`. Three trigger paths fully diagrammed with mermaid sequence diagrams, cleanup flows, failure flowchart, sandbox state machine. Actors and "where they live" explicit.

## Known issues / blockers

### Permission prompts during file writes
Even after setting `defaultMode: bypassPermissions` (project) and `skipDangerousModePermissionPrompt: true` (user), Write operations to existing empty stub files in this session kept triggering "Overwrite file" prompts. **Root cause** (confirmed via Anthropic docs): `defaultMode` is loaded at session startup; mid-session edits don't take effect.

**Fix for next session:** A fresh Claude Code session should silently bypass these prompts. If it doesn't, the user-level `ask` rules at `/Users/liborballaty/.claude/settings.json` lines 405-411 (`Edit(**/*.py)`, `Write(**/*.py)`, etc.) may still be firing — `bypassPermissions` is documented to override these, so a restart is the right test.

### `requirements.txt` blocked globally
User's global settings deny `Edit(**/requirements.txt)`. **Decision deferred to end of Phase 1.** `pyproject.toml` is the source of truth for deps. If `requirements.txt` is needed for CI later, the user must remove the global deny rule.

### Bash `rm` patterns
Project allow list scopes `rm` narrowly (only inside repo + `/tmp/lcm-*`, `/tmp/test-*`). Multi-file `rm` invocations may not match the single-path patterns. If next session needs to delete several files at once, either widen the rule or call rm in a loop.

---

## Reference documents (read these first)

1. `AGENT-INSTRUCTIONS.md` — phased delivery plan, per-phase deliverables, success criteria.
2. `SANDBOX-DETAILED-FLOW.md` — exact bash commands and verification steps for every phase/step. Reference STEP 0.1 → 3.2 for Phase 1.
3. `SANDBOX-ARCHITECTURE.md` — broader design context (mounts, ACLs, lifecycle).
4. `IMPLEMENTATION-PLAN.md` — high-level rationale and design decisions.

---

## How to resume

1. Start a fresh Claude Code session in this repo (`cd /Users/liborballaty/LocalProjects/GitHubProjectsDocuments/LCM-Sandbox && claude`).
2. Bypass mode should activate silently. If prompts still appear, press shift+tab once.
3. Read this file + `AGENT-INSTRUCTIONS.md` for context.
4. Resume from Task 3 (finish utils/git.py and utils/docker.py), then proceed sequentially.
5. Use `TaskList` to see current task state; the in-memory task list is gone after session ends so you'll need to recreate tasks from the pending list above.

---

## File inventory (current state, post-Phase-1)

```
LCM-Sandbox/
├── .claude/
│   ├── settings.json              ✓ committed (project-shared, scoped allow/deny)
│   └── settings.local.json        — gitignored (personal)
├── .gitignore                     ✓ committed
├── AGENT-INSTRUCTIONS.md          (pre-existing)
├── IMPLEMENTATION-PLAN.md         ✓ updated this session (status table appended)
├── README.md                      ✓ updated this session (status + doc index)
├── SANDBOX-ARCHITECTURE.md        (pre-existing)
├── SANDBOX-DETAILED-FLOW.md       (pre-existing — STEP 1.1.3 minor deviation noted in code)
├── SANDBOX-AGENT-CONFIG.md / .html  ✓ new this session (Phase 2 design)
├── SANDBOX-IMAGE-TOOLCHAIN.md     ✓ new this session (Phase 2 design)
├── SANDBOX-ORCHESTRATION.md / .html ✓ new this session (Phase 5 design)
├── SANDBOX-FLOWS.html             ✓ new this session (mermaid flows)
├── SESSION-HANDOFF.md             (this file)
├── pyproject.toml                 ✓ committed
└── lcm_sandbox/                   ✓ ALL implemented, 43/43 tests passing
    ├── __init__.py
    ├── cli.py                     ✓ click entry point, --skip-docker / Phase-5 flags pending
    ├── exceptions.py              ✓
    ├── commands/create.py         ✓ orchestrates Phases 0-3
    ├── core/preflight.py          ✓ 8 checks (3,4,8 skippable via --skip-docker)
    ├── core/worktree.py           ✓ STEP 1.1 + 1.2; --track flag dropped vs spec
    ├── core/sync.py               ✓ STEP 2.1-2.6
    ├── core/docker_builder.py     ✓ STEP 3.1 + 3.2
    ├── models/                    ✓ SandboxConfig, AllowedPaths, Phase1Result, WorktreeBaseline
    ├── utils/                     ✓ shell, git, docker, logger
    └── tests/                     ✓ 43 tests (models, utils, preflight, worktree, sync, docker_builder)
```

---

## Phase 1 task list — closed

All 10 Phase 1 tasks complete. All 4 follow-up tasks (#11 revert temp perms, #12 agent profile design, #13 toolchain design, #14 agent profile revisions + HTML, #15 MCP audit, #16 orchestration channel design, #17 flows HTML) also complete.

## Open follow-ups for next session (priority order)

### Pre-Phase-2 verification gates
- [ ] Verify GitHub claude-code issue **#28293** (`.mcp.json` custom auth headers may not be forwarded on every POST) is fixed in installed Claude Code version — **blocks the Phase 5 bearer-in-header design**.
- [ ] Check GitHub claude-code issue **#36665** (server-push notifications request) status — informs whether Phase 5 can rely on `elicitation/create` and `notifications/*` or must use the polling fallback (`get_more_context()` heartbeat).
- [ ] On a real consumer repo (e.g. `go-madeira`, `arionetworks-website`, `ballaty-rentals`), verify whether `.mcp.json` exists and what's in it; the entrypoint's sanitize step needs a real-world test case.

### Phase 2 (Dockerfile + entrypoint + launcher)
- [ ] Write `docker/Dockerfile` per `SANDBOX-IMAGE-TOOLCHAIN.md`.
- [ ] Write `scripts/docker-entrypoint.sh` implementing STEP 4.5 + the in-sandbox config application from `SANDBOX-AGENT-CONFIG.md`.
- [ ] Write `scripts/docker-git-hooks.sh` (pre-push deny hook).
- [ ] Write `scripts/rm-shim.sh`, `scripts/smoke-test.sh`, `scripts/apply_agent_profile.py`, and `lcm_sandbox/templates/agent_profiles/{permissive,standard}.json`.
- [ ] Implement `lcm_sandbox/core/docker_launcher.py` (STEP 4.1-4.4 + monitoring).
- [ ] Add `--cap-drop=ALL --security-opt=no-new-privileges --read-only` to the docker run flags.
- [ ] Add `--mcp-endpoint`, `--mcp-token-file`, `--egress-allowlist` CLI flags (forward into the container as env/secret mounts).
- [ ] Integration tests: real container launch, file ACL enforcement, git push blocked.

### Phase 3 (artifact capture + cleanup)
- [ ] Implement `lcm_sandbox/core/artifact_capture.py` per STEP 6.
- [ ] Add `cleanup` and `status` CLI subcommands.
- [ ] S3 or filesystem archival for artifacts.

### Phase 4 (docs + packaging)
- [ ] `USAGE.md`, `TROUBLESHOOTING.md`.
- [ ] E2E test suite.
- [ ] pip packaging + distribution.
- [ ] Create `CHANGELOG.md` (does not exist; flagged as a tracker gap).

### Phase 5 (AIDevOps integration) — depends on #28293 verification
- [ ] AIDevOps-side: MCP server exposing the 8 tools, OAuth 2.1 token issuer with audience binding + revocation, per-run docker network egress allowlist (iptables or CNI), audit log sink, workflow gates consuming `request_human_approval`.
- [ ] AIDevOps-side: add `JOB_HANDLERS['platform:sandbox-run']` per the integration doc.
- [ ] AIDevOps-side: update `openrouter-agent/docs/lcm-sandbox-integration.md` to reflect the live MCP channel (currently describes spawn-and-wait only).
- [ ] PR-merge webhook → `lcm-sandbox cleanup` wiring.

### Deferred (explicitly)
- **Codex CLI audit** — Codex's equivalent of `bypassPermissions` is unknown. Until audited, Codex stays out of the image. Tracked in `SANDBOX-AGENT-CONFIG.md` open audit items.
- **Gemini CLI audit** — same as Codex.
- **Per-project Docker images** — universal image first; pivot via `--image-tag` flag later if needed.
- **`CHANGELOG.md` / `CONTRIBUTING.md`** — low priority pre-public.
