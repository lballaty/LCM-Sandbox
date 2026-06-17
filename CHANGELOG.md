# Changelog

All notable changes to LCM-Sandbox are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) once it leaves pre-public status.

## [Unreleased]

### Added — Plan Phase 1 (housekeeping)
- Structured remaining-work plan for `/execute-plan` autonomous mode (`PLAN-REMAINING-WORK.md`) — `c2aa8e2`.
- Phase 5 prerequisite verification doc (`docs/PHASE-5-PREREQS-VERIFICATION.md`) recording resolution of GH `claude-code` issues #28293 and #36665.
- `CHANGELOG.md` itself.

### Added — Plan Phase 2 (autonomous launcher hardening)
- `scripts/docker-git-hooks.sh` — pre-push deny hook installer. Handles both normal repos (`.git` is a directory) and worktrees (`.git` is a file with a `gitdir:` line, including relative-path form). Fixes a latent bug where the previous inline entrypoint logic silently skipped worktrees. 6 unit tests in `test_docker_git_hooks.py`.
- `scripts/rm-shim.sh` — defensive `rm` wrapper installed at `/usr/local/bin/rm`. Forwards to `/bin/rm` for paths under a safelist (`/workspace`, `/tmp`, `/var/tmp`, `/home/aiagent/.cache`, `/home/aiagent/.local`); rejects everything else including symlink escapes (canonicalises via `readlink -f`). 6 unit tests in `test_rm_shim.py`.
- `scripts/smoke-test.sh` — bootstrap smoke test that refuses to drop to the agent if any required pre-condition fails (UID 1000, `/workspace` mount, `.sandbox-manifest.json`, pre-push hook present, rm-shim resolution, at least one agent CLI on PATH).
- `lcm_sandbox/templates/agent_profiles/{permissive,standard}.json` — canonical in-sandbox Claude Code settings from `SANDBOX-AGENT-CONFIG.md`.
- `scripts/apply_agent_profile.py` — renders the requested profile into `/home/aiagent/.claude/settings.json` based on `$LCM_AGENT_PROFILE`. `LCM_PROFILE_TEMPLATE_DIR` env var overrides the template location so the in-container entrypoint can point at the image-baked path. 7 unit tests in `test_apply_agent_profile.py`.
- `--read-only` rootfs in `docker_launcher.py` with explicit `--tmpfs` mounts for `/tmp`, `/var/tmp`, `/run`, and `/home/aiagent/.cache`.
- `--egress-allowlist HOST[:PORT][,HOST[:PORT]]…` CLI flag on `lcm-sandbox launch`. Parsed via `parse_egress_allowlist()`; forwarded into the container as `LCM_EGRESS_ALLOWLIST` env var. Actual network enforcement is Phase 5 infrastructure work (requires a host-side restricted bridge because `--cap-drop=ALL` blocks in-container iptables); the flag plumbing is in place.
- Integration test `test_integration_hardening_assertions` that asserts `/workspace` writable, rootfs + `/etc` read-only, `/tmp` writable, pre-push hook rejects pushes. Gated on the Hermes image being built locally; skipped otherwise.

### Changed — Plan Phase 2
- `scripts/entrypoint.sh` — refactored to delegate to `docker-git-hooks.sh`, `apply_agent_profile.py`, and `smoke-test.sh` (in steps 4.5.6, 4.5.7a, 4.5.7b). Smoke-test failure now hard-fails the entrypoint via `die`.
- `scripts/Dockerfile.hermes` — copies the four new scripts plus the agent profile templates into `/opt/lcm-sandbox/`; installs `rm-shim.sh` as `/usr/local/bin/rm`.

### Changed
- `SESSION-HANDOFF.md` status summary now reflects the post-`9cda868` test count (57 passing / 0 failing / 1 skipped); the pre-existing "7 failing persona tests" follow-up is marked closed.
- `SESSION-HANDOFF.md` Pre-Phase-2 verification gates section: both GH `claude-code` gates checked off with resolution date and a pointer to the verification doc.

### Test suite
- 85 passed, 2 skipped (was 57/0/1). Skips are the two Hermes-image-gated integration tests.

## [0.5.0] — 2026-06-16 — Dev-sandbox productionization

### Added
- Single-entry-point host CLI for the manual dev-sandbox flow (`~/.ai-dev-dotfiles/bin/dev-sandbox`, lives in the dotfiles repo; commit `116adb9` there). Subcommands: `create`, `verify`, `restart`, `stop`, `list`, `enter`, `help`. Designed to be the only command allowlisted for autonomous agent use of the manual flow.
- `scripts/setup-dev-sandbox.sh` — idempotent setup; pins `--context colima-lcm-sandbox`; pre-flights VM DNS to `deb.nodesource.com`; builds image if missing.
- `scripts/verify-dev-sandbox.sh` — 11-check in-container suite (CLI versions, git identity, `/workspace` r/w, dotfile mounts). Non-zero exit on any fail.
- `scripts/stop-dev-sandbox.sh` — refuses unless image matches `lcm-dev-sandbox:latest`; warns if `lcm-dev-sandbox=managed` label is missing.

### Changed
- `scripts/run-dev-sandbox.sh` — pinned `--context colima-lcm-sandbox`; added `--label lcm-dev-sandbox=managed` for ownership tracking; rejects `REPO_PATH` outside `$HOST_HOME` with a clear error (Colima only mounts `/Users/<HOST_USER>` into the Lima VM).
- `scripts/Dockerfile.dev-sandbox` — each agent CLI install (Claude Code / Codex / Gemini) runs in its own `RUN` so single-package failures fail the build instead of being silently masked by `|| true`.

### Verified
- 11/11 verification checks PASS on a real container against `colima-lcm-sandbox`.

Commit: `e81e700`.

## [0.4.0] — 2026-06-15 — Hardening + persona test fix

### Added
- `--security-opt=no-new-privileges` flag in `lcm_sandbox/core/docker_launcher.py`.
- Autouse `NO_PROXY` fixture in `lcm_sandbox/tests/conftest.py` so the in-process `HTTPServer` fixture in `test_persona_render_capture.py` survives local Privoxy interception.
- Test assertion for the new `--security-opt` flag in `test_docker_launcher.py`.

### Fixed
- 7 persona test failures — root cause was local Privoxy intercepting `http://127.0.0.1:*/api/personas`. Test suite went from 50/7/1 to **57/0/1**.

### Deferred (with explicit TODO in code)
- `--read-only` rootfs + explicit `--tmpfs /tmp` and `--tmpfs /home/aiagent/.cache` for the autonomous launcher.
- `--egress-allowlist` CLI flag.

Commit: `9cda868`.

## [0.3.0] — 2026-06-15 — Manual dev-sandbox template (v1) + tracker reconciliation

### Added
- `scripts/Dockerfile.dev-sandbox` — ubuntu:24.04 + Node 20 + Python + uv + Claude Code + Codex CLI + Gemini CLI. Bakes `/Users/<HOST_USER>` → `/home/aiagent` symlink so host-absolute paths in mounted dotfiles resolve.
- `scripts/run-dev-sandbox.sh` — launcher mounting host dotfiles rw (`.claude`, `.codex`, `.gemini`) so OAuth state persists; `.ai-dev-dotfiles` and `.gitconfig` ro.
- `scripts/README-dev-sandbox.md` — end-to-end runbook for the manual flow.
- `/dev-sandbox` Claude Code skill at `~/.claude/skills/dev-sandbox/SKILL.md` (committed in dotfiles repo as `9dff4e4`).

### Changed
- Tracker reconciliation across `README.md`, `IMPLEMENTATION-PLAN.md`, `SESSION-HANDOFF.md` — pre-existing Phase 2 / Phase 4 / WP-8 work was uncommitted and undocumented; now all rows reflect committed state with commit hashes.

Commits: `51c77e2` (template), `4682e94` (post-commit tracker reconcile).

## [0.2.0] — Phase 2 + Phase 4 + WP-8 (committed 2026-06-15, work landed earlier)

### Added — Phase 2 image (`829ff5a`)
- `scripts/Dockerfile.hermes` — Ubuntu 24.04 base with Hermes Agent baked in. Standalone variant; not based on `lcm-dev-agent`.
- `scripts/entrypoint.sh` — WP-2 entrypoint that runs as root to chown the bind-mounted workspace then drops to `aiagent` before launching Hermes.
- `scripts/build-hermes-image.sh` — image build helper.

### Added — Phase 4 launcher (`51697aa`)
- `lcm_sandbox/core/docker_launcher.py` — `launch_container`, `stop_container`, `container_status`. Implements STEP 4.1–4.4 + monitoring. Hermes-aware (`--hermes-persona`, MCP URL/token, model provider/key).
- `lcm-sandbox launch | stop | status` CLI subcommands.
- New exit code `5` for `DockerLaunchError`.

### Added — WP-8 HERMES persona (`c0b0bb7`)
- `lcm_sandbox/persona/renderer.py` + `capturer.py` + `cli.py` — materializes persona-owned files (config.yaml, SOUL.md, MEMORY.md, .env, skills/) from the AIDevOps platform API + persona repo, and detects post-run mutations for `proposed_persona_changes`.
- `persona-state-renderer` + `persona-state-capturer` console scripts.
- `responses>=0.25` dev dependency.
- `uv.lock` (tooling switch from pip-only).

### Tests
- 50 unit tests pass; 7 persona tests red on machines running a local Privoxy proxy (fixed in 0.4.0); 1 integration test skipped pending a locally-built `lcm-hermes-agent:latest`.

## [0.1.0] — 2026-05-XX — Phase 1: core CLI

### Added
- Initial implementation of Phases 0–3 from `SANDBOX-DETAILED-FLOW.md`: preflight checks, worktree create/reset, sync to latest main, docker image presence + functional check.
- `lcm-sandbox create` CLI command + Pydantic models (`SandboxConfig`, `AllowedPaths`, `WorktreeBaseline`, `Phase1Result`).
- 43 unit tests passing; end-to-end sanity-checked against a temp git repo.

Commit: `39e6517`.

## [0.0.x] — Pre-Phase-1 design docs

- Architecture, detailed flow, implementation plan documents.
- Agent instructions for autonomous implementation.
- Phase 2 in-sandbox agent permission profile design + image toolchain spec.
- Phase 5 secure AIDevOps ↔ sandbox channel design + end-to-end flow diagrams.
- Project-shared Claude Code settings.

Commits: `bc270b7`, `0250c3f`, `900c70f`, `53e63b8`, `1b0ed64`, `f3ac395`.
