# Plan — Remaining Work (LCM-Sandbox)

**File:** `PLAN-REMAINING-WORK.md`
**Description:** Structured execution plan for everything still open as of 2026-06-16. Designed to be consumed by `/execute-plan` in autonomous mode. Tasks are grouped into five phases with explicit dependencies; each task has a single deliverable, a verifiable acceptance criterion, and references to the design docs that define correctness.
**Author:** Libor Ballaty <libor@arionetworks.com>
**Created:** 2026-06-16
**Last Updated:** 2026-06-16
**Last Updated By:** Libor Ballaty

---

## How to read this plan

- **Phases** are coherent units that share a goal. Run them in order — later phases depend on earlier ones.
- **Tasks** within a phase are mostly independent and can be parallelized, except where noted.
- **Acceptance criterion** for every task is a single command or assertion the executor can run unattended to decide pass/fail. No "looks good" criteria.
- **References** point at the design docs that already specify correctness. The executor should read them before implementing — do not re-design what is already designed.
- **Deferred** at the end is work that is intentionally not in scope for this plan.

When a task changes a tracker (`SESSION-HANDOFF.md`, `IMPLEMENTATION-PLAN.md`, `README.md`), the executor must reserve via `queuectl` first (per repo write-coordination policy).

The full test suite must remain green at the end of every phase: `venv/bin/python -m pytest lcm_sandbox/tests/ -q` should report `57 passed, 1 skipped` (or better — the skip count may shrink as the integration test gets unblocked) and exit 0.

---

## Phase 1 — Housekeeping (≈30 min, no dependencies)

Closes pre-existing small gaps before tackling feature work.

### Task 1.0 — Verify dev-sandbox lockdown holds after fresh session start

**Why**
`SESSION-HANDOFF.md` records a permission-cache caveat: during the 2026-06-16 productionization session, direct `docker exec` / `docker stop` calls succeeded despite no matching allow rule on disk, because Claude Code's permission cache retained transient rules. On a fresh session this should fail. The lockdown design says only `Bash(/Users/liborballaty/.ai-dev-dotfiles/bin/dev-sandbox:*)` is allowlisted.

**Deliverable**
A short verification record appended to `SESSION-HANDOFF.md` under "Manual dev-sandbox template — current state" documenting:
- direct `docker exec …` → expected denied with Claude permission error
- `~/.ai-dev-dotfiles/bin/dev-sandbox list` → expected allowed and returns 0
- timestamp of the check

**Acceptance criterion**
Two lines in `SESSION-HANDOFF.md`: one recording the denial result for direct docker, one recording success for the wrapper.

**How**
The executor's first two Bash calls in this phase should be (in order) a direct `docker exec` against a non-existent container (the goal is to observe the permission decision, not the docker outcome) and the wrapper's `list` subcommand. Record outcomes.

### Task 1.0.1 — Sync stale test-count in `SESSION-HANDOFF.md`

**Why**
The handoff still reads "50 passing, 7 failing, 1 skipped" but the suite is actually **57 passing, 0 failing, 1 skipped** since commit `9cda868` (Privoxy bypass fix).

**Deliverable**
The status-summary line in `SESSION-HANDOFF.md` is updated to reflect 57/0/1, with a parenthetical noting that the Privoxy issue was closed by the autouse `NO_PROXY` fixture in `conftest.py`.

**Acceptance criterion**
`grep -E "57 passing" SESSION-HANDOFF.md` succeeds and `grep -E "7 failing" SESSION-HANDOFF.md` returns no matches.

### Task 1.1 — Remove stray image on `colima-backups`

**Why**
The build earlier today landed on the wrong Colima context (`colima-backups`) and left an unwanted `lcm-dev-sandbox:latest` image there. Harmless but worth cleaning.

**Deliverable**
The image `lcm-dev-sandbox:latest` is absent from the `colima-backups` Docker context.

**Acceptance criterion**
```
docker --context colima-backups image inspect lcm-dev-sandbox:latest 2>&1 | grep -q "No such image"
```
exits with status 0.

**How**
```
docker --context colima-backups rmi lcm-dev-sandbox:latest
```
If the image is in use by a container on that context, stop/remove the container first.

### Task 1.2 — Verify GH `claude-code` issue #28293

**Why**
Phase 5 design depends on whether `.mcp.json` custom auth headers are forwarded on every POST. If still broken, the bearer-in-header pattern in `SANDBOX-ORCHESTRATION.md` needs the stdio-proxy workaround.

**Deliverable**
A new file `docs/PHASE-5-PREREQS-VERIFICATION.md` (single file in a new `docs/` directory) recording: the installed Claude Code version, what was tested, what was observed, and a verdict (`fixed` / `still broken` / `cannot determine without backend`).

**Acceptance criterion**
The file exists, contains a `## Verdict` section, and `SESSION-HANDOFF.md` "Pre-Phase-2 verification gates" bullet has been updated to reference it.

**How**
1. `claude --version` — record the version.
2. Read the issue at `https://github.com/anthropics/claude-code/issues/28293` via WebFetch — note its current status.
3. If the issue is closed/fixed in the installed version, write a `fixed` verdict; otherwise record a `still broken` verdict with the linked workaround from `SANDBOX-ORCHESTRATION.md` §2.

### Task 1.3 — Verify GH `claude-code` issue #36665

**Why**
Same rationale as 1.2 — Phase 5 needs to know whether to design around polling fallback or rely on server-push notifications.

**Deliverable**
Same file as 1.2 (append a second section for #36665).

**Acceptance criterion**
The file's `## Issue #36665` section exists with a verdict.

**How**
Same pattern as 1.2 — fetch the issue, record observed status, write verdict.

### Task 1.4 — Adopt `CHANGELOG.md`

**Why**
Flagged as a gap in previous handoffs. The project has enough committed history now (12 commits since Phase 1) to warrant a real changelog.

**Deliverable**
`CHANGELOG.md` at repo root following the Keep-a-Changelog format. Seed it with one entry per logical milestone reverse-chronologically — Phase 1 / Phase 2 image / Phase 4 launcher / WP-8 persona / dev-sandbox template / hardening + proxy fix / dev-sandbox productionization.

**Acceptance criterion**
`grep -E '^## \[Unreleased\]' CHANGELOG.md && grep -E '^## \[0\.' CHANGELOG.md` both succeed.

**References**
- https://keepachangelog.com/en/1.1.0/
- Each entry must cite commit hashes from `git log --oneline`.

---

## Phase 2 — Autonomous launcher hardening (≈2–3 hours)

Finishes the security posture promised in design docs for the autonomous `lcm-sandbox` flow. Phase 1 is not a hard prerequisite, but doing Phase 1 first keeps the working tree tidy.

### Task 2.1 — `scripts/docker-git-hooks.sh` (pre-push deny hook)

**Why**
`SANDBOX-AGENT-CONFIG.md` requires that commits inside the sandbox are allowed but pushes to origin are blocked at the git-hook level (defence-in-depth alongside container egress rules).

**Deliverable**
`scripts/docker-git-hooks.sh` — installs a `pre-push` hook into `/workspace/.git/hooks/` (and any worktree linked hooks dir) that rejects every push with a clear error message.

**Acceptance criterion**
Inside the running `lcm-hermes-agent` container:
```
git push 2>&1 | grep -q "pushes are blocked"
```
returns exit code 0 and `git push` itself exits non-zero.

**References**
- `SANDBOX-AGENT-CONFIG.md` §4 "Layered defenses"
- `SANDBOX-DETAILED-FLOW.md` STEP 4.5.6

### Task 2.2 — `scripts/rm-shim.sh`

**Why**
Listed as defensive layer in `SANDBOX-AGENT-CONFIG.md`. Wraps `rm` so that destructive paths outside `/workspace` (specifically anything starting with `/`, `/home/aiagent`, `/opt/lcm-sandbox`) are rejected. Inside `/workspace` `rm` is unrestricted.

**Deliverable**
`scripts/rm-shim.sh` plus a Dockerfile.hermes change that copies it to `/usr/local/bin/rm` ahead of the system `rm` on PATH.

**Acceptance criterion**
Inside the container:
- `rm /home/aiagent/.claude/credentials.json` fails with a clear error.
- `rm /workspace/test.txt` succeeds (after `touch /workspace/test.txt`).

### Task 2.3 — `scripts/smoke-test.sh` (in-container bootstrap)

**Why**
`SANDBOX-AGENT-CONFIG.md` requires a bootstrap smoke test that runs once on container start and refuses to drop to the agent if any check fails (catches missing dotfiles, wrong UID, busted hooks).

**Deliverable**
`scripts/smoke-test.sh` plus an entrypoint hook that runs it after dotfile mounts but before launching the agent. On failure, the entrypoint logs the failed check and exits non-zero.

**Acceptance criterion**
- Forcing a check to fail (e.g. unmounting `/workspace/.git` temporarily) causes the container to exit with a non-zero code containing the failed check name in stderr.
- All checks pass on a normally-configured container; the agent launches normally.

### Task 2.4 — Agent profile templates + `scripts/apply_agent_profile.py`

**Why**
`SANDBOX-AGENT-CONFIG.md` defines two profiles (`permissive` default, `standard` opt-in). Currently both are documented but no JSON templates exist and no code applies them.

**Deliverables**
- `lcm_sandbox/templates/agent_profiles/permissive.json`
- `lcm_sandbox/templates/agent_profiles/standard.json`
- `scripts/apply_agent_profile.py` — reads `LCM_AGENT_PROFILE` env var (default `permissive`), merges the corresponding JSON into `/home/aiagent/.claude/settings.json`.
- Wire into `entrypoint.sh` after the `/workspace` mount is verified.

**Acceptance criterion**
- `LCM_AGENT_PROFILE=standard` produces a `~/.claude/settings.json` containing the keys from `standard.json`.
- Default (no env var) produces the permissive set.
- Schema is validated against the documented 13-row coverage matrix in `SANDBOX-AGENT-CONFIG.md`.

### Task 2.5 — `--read-only` rootfs + tmpfs in `docker_launcher.py`

**Why**
Promised in Phase 4 design and currently deferred with a TODO. The launcher needs `--read-only` plus explicit `--tmpfs /tmp` and `--tmpfs /home/aiagent/.cache` so Hermes' write paths still function.

**Deliverable**
`docker_launcher.py` adds the three flags. The existing docstring TODO is removed. The `test_docker_launcher.py` asserts all three flags appear in the argv.

**Acceptance criterion**
- `pytest lcm_sandbox/tests/test_docker_launcher.py -q` passes.
- A real container launch (Hermes mode) completes the smoke test in 2.3 without rootfs write errors.

### Task 2.6 — `--egress-allowlist` CLI flag

**Why**
Phase 4 follow-up. The launcher currently uses the default Docker bridge; the CLI flag should let the caller pass a comma-separated list of host:port pairs that the container is allowed to reach (everything else rejected via iptables in the sandbox network).

**Deliverable**
- New CLI option `--egress-allowlist HOST:PORT[,HOST:PORT...]` on the `launch` command.
- A `_install_egress_rules` helper in `docker_launcher.py` that emits the iptables commands inside the container during entrypoint init.
- Skip rule installation cleanly if the flag is absent (current behavior).

**Acceptance criterion**
- Without the flag: behavior unchanged (default bridge).
- With `--egress-allowlist api.openai.com:443`: `curl https://example.com` inside the container times out; `curl https://api.openai.com/v1/models` reaches the upstream.

### Task 2.7 — Real-container integration test for the launcher

**Why**
The integration test in `test_docker_launcher.py:206` is currently skipped because `lcm-hermes-agent:latest` isn't built locally. After tasks 2.1–2.6 land, this test should pass.

**Deliverable**
The skip condition stays (it's correct — CI without the image should skip), but a developer can build the image and the test passes end-to-end. Add a new test that explicitly verifies: workspace is writable, host home is not, git push is blocked, rootfs is read-only.

**Acceptance criterion**
With the Hermes image built locally:
```
venv/bin/python -m pytest lcm_sandbox/tests/test_docker_launcher.py -q
```
reports `<n> passed` and 0 skipped.

---

## Phase 3 — Artifact capture + cleanup (≈3–4 hours)

Closes the autonomous-flow loop: create → launch → capture → cleanup. After Phase 3, the `lcm-sandbox` CLI is feature-complete for local use; Phase 5 (AIDevOps wiring) is a separate concern.

Depends on: Phase 2 (the captured artifacts include things the hardened entrypoint produces).

### Task 3.1 — `lcm_sandbox/core/artifact_capture.py`

**Why**
`SANDBOX-DETAILED-FLOW.md` STEP 6 is fully specified but unimplemented. Captures the new commits, the unified diff, stdout/stderr logs, manifest, and the worktree HEAD baseline at end-of-run.

**Deliverable**
A module exposing `capture_artifacts(sandbox_config, *, output_dir) -> ArtifactCaptureResult` plus a Pydantic `ArtifactCaptureResult` model with: commit hashes, diff path, log paths, manifest path, capture timestamp, exit code.

**Acceptance criterion**
Unit tests covering the success path, the "no commits" path, and the "container still running" failure path all pass.

**References**
- `SANDBOX-DETAILED-FLOW.md` STEP 6.1–6.5
- `lcm_sandbox/models/artifact.py` (`WorktreeBaseline`, `Phase1Result`) — extend, do not duplicate

### Task 3.2 — `cleanup` CLI subcommand

**Why**
Promised in design; trivial wrapper around `git worktree remove` plus `docker rm` plus artifact archival cleanup.

**Deliverable**
`@main.command("cleanup")` in `lcm_sandbox/cli.py` accepting `--sandbox-id` and optional `--keep-artifacts` (default false). Removes the worktree, removes the container if still present, deletes artifact dir unless `--keep-artifacts`.

**Acceptance criterion**
- `lcm-sandbox cleanup --sandbox-id <id>` exits 0 after a successful run.
- Re-running it (idempotency) exits 0 and prints "already cleaned up".
- `--keep-artifacts` leaves the artifact dir on disk.

### Task 3.3 — Artifact archival path (filesystem only)

**Why**
S3 is deferred to Phase 5 (per `IMPLEMENTATION-PLAN.md`). The first iteration archives to `~/.lcm-sandbox/artifacts/<sandbox_id>/` on the host.

**Deliverable**
- `~/.lcm-sandbox/artifacts/` is created on first run.
- Per-sandbox subdirectory contains: `manifest.json`, `commits.json`, `diff.patch`, `stdout.log`, `stderr.log`, `agent.log`.
- A short README at `~/.lcm-sandbox/artifacts/README.md` documenting the layout (regenerated on each first-run if missing).

**Acceptance criterion**
After a successful `lcm-sandbox create → launch → cleanup --keep-artifacts` sequence, the per-sandbox directory contains all six expected files.

### Task 3.4 — Tests + tracker updates

**Why**
Phase 3 is not done until the tests are green and `IMPLEMENTATION-PLAN.md` reflects it.

**Deliverable**
- Unit tests for `artifact_capture` and `cleanup` reach ≥ 90 % line coverage.
- `IMPLEMENTATION-PLAN.md` Phase 3 row flipped from ⬜ to ✅ with commit hash.
- `README.md` status table updated.
- `CHANGELOG.md` entry under Unreleased.

**Acceptance criterion**
`venv/bin/python -m pytest lcm_sandbox/tests/ -q` passes with the new tests; `grep -E "Phase 3.*✅" IMPLEMENTATION-PLAN.md` succeeds.

---

## Phase 4 — WP-8 persona reconciliation (≈1–2 hours)

The persona renderer/capturer code is committed but its design doc lives outside this repo and the feature is invisible in `IMPLEMENTATION-PLAN.md` beyond a status row. Closes that gap.

No code dependencies on earlier phases; can be done in parallel with Phase 2 or 3.

### Task 4.1 — Locate or stub `HERMES-PERSONA-INTEGRATION-PLAN.md`

**Why**
`lcm_sandbox/persona/__init__.py` references it as the source of truth but the file is not in this repo. Either commit the real one here or write a stub that links to wherever it lives.

**Deliverable**
`HERMES-PERSONA-INTEGRATION-PLAN.md` at repo root. If the real document can be located (search the user's other repos: `aidevops`, `xLLMArionComply`, etc.), copy it here. Otherwise write a brief stub pointing at the external location with a TODO to consolidate.

**Acceptance criterion**
`ls HERMES-PERSONA-INTEGRATION-PLAN.md` succeeds and the file is at least 200 lines OR contains an explicit `> **External canonical source:** <link>` line.

### Task 4.2 — Add a Persona section to `IMPLEMENTATION-PLAN.md`

**Why**
Currently WP-8 has one status-table row but no narrative or design summary in the plan, so a new reader can't tell what the feature does.

**Deliverable**
A new `### WP-8: HERMES Persona Subsystem` section under `## Component Breakdown` describing inputs (platform API + persona repo), outputs (rendered files + change events), and the relationship to Hermes runtime.

**Acceptance criterion**
`grep -E "^### WP-8" IMPLEMENTATION-PLAN.md` succeeds.

---

## Phase 5 — Documentation polish (≈1 hour)

Pure docs work. Independent of all other phases.

### Task 5.1 — `USAGE.md`

**Why**
End-user-facing quickstart that doesn't require reading the design docs. Worth having before any external contributor uses the tool.

**Deliverable**
`USAGE.md` at repo root covering: install, prerequisites (Colima profile setup), `create`/`launch`/`status`/`stop`/`cleanup` happy-path examples for the autonomous CLI, and a separate section for the manual `dev-sandbox` flow.

**Acceptance criterion**
File exists; running through the documented commands top-to-bottom against the verified setup (Phase 1 complete) produces a successful sandbox lifecycle.

### Task 5.2 — `TROUBLESHOOTING.md`

**Why**
Captures the gotchas we discovered in this session (TTY-less docker exec, `/login` persistence, Codex RO warning, hook path resolution, wrong Colima context, `HOST_HOME` mount constraint) plus those documented elsewhere.

**Deliverable**
`TROUBLESHOOTING.md` at repo root organized by symptom → cause → fix. Cross-reference `scripts/README-dev-sandbox.md` gotchas table.

**Acceptance criterion**
File exists with at least 6 entries.

---

## Out of scope (explicitly deferred — do not touch in this plan)

- **Phase 5 AIDevOps integration implementation** — depends on Task 1.2 + 1.3 verification verdicts; revisit only after those land.
- **Codex CLI / Gemini CLI agent profiles** — `SANDBOX-AGENT-CONFIG.md` explicitly defers these; do not add to `apply_agent_profile.py` in Phase 2.
- **S3 / cloud artifact archival** — Phase 3 ships filesystem-only; cloud storage is Phase 5+.
- **pip packaging + PyPI publish** — original Phase 4 in `IMPLEMENTATION-PLAN.md`. Not blocking and the project is pre-public.
- **Per-project Docker image variants** — universal image is sufficient; `--image-tag` already supports custom images for callers that need it.
- **CONTRIBUTING.md** — pre-public, not needed.

---

## Execution order summary

```
Phase 1 (Housekeeping)              → independent; do first
Phase 2 (Launcher hardening)        → independent of P1 functionally; do second for cleanliness
Phase 3 (Artifact capture)          → depends on Phase 2
Phase 4 (Persona reconciliation)    → independent; parallelize with P2 or P3
Phase 5 (Documentation polish)      → independent; do last so docs reference final state
```

Each phase ends with a tracker update + push to `origin/main`. `/execute-plan` should commit per task using the existing conventional-commit style (`feat(area): ...`, `docs(area): ...`, `test(area): ...`), and run the full test suite as a gate between phases.
