# LCM-Sandbox — Troubleshooting

**File:** `TROUBLESHOOTING.md`
**Description:** Symptom → cause → fix table for the gotchas accumulated while building and running both LCM-Sandbox flows. Organised so you can `grep` for the error message you're seeing.
**Author:** Libor Ballaty <libor@arionetworks.com>
**Created:** 2026-06-17
**Last Updated:** 2026-06-17
**Last Updated By:** Libor Ballaty

---

## Manual `dev-sandbox` flow

### `docker exec -it … bash` exits with `the input device is not a TTY`

- **Cause:** You ran the command through Claude Code's `!` bash prompt (or any other no-TTY harness). `docker exec -it` requires a real terminal.
- **Fix:** Open a regular terminal and re-run it there.

### `/login` succeeds but the next command says `Not logged in`

- **Cause:** `~/.claude` mounted read-only — `/login` writes a refreshed OAuth token to `~/.claude/.credentials.json`, the write silently fails, and the next request finds no valid token.
- **Fix:** Recreate the container with the updated launcher; `scripts/run-dev-sandbox.sh` mounts `.claude`, `.codex`, and `.gemini` read-write by design.

### Codex prints `WARNING: ... Read-only file system (os error 30)`

- **Cause:** Same root cause as the previous gotcha. Codex tries to write its PATH-alias file on first run.
- **Fix:** Recreate with rw `.codex` mount, or ignore the warning if you don't need the aliases.

### Claude hook script fails with `No such file or directory: /Users/<you>/.claude/hooks/...`

- **Cause:** The image was built before the `HOST_USER` symlink was added, so absolute host paths in the hook config don't resolve inside the container.
- **Fix:** Rebuild the image. The current `Dockerfile.dev-sandbox` creates `/Users/<HOST_USER> → /home/aiagent` at build time.

### `Container won't start — name conflict`

- **Cause:** A container with that name already exists. The launcher refuses to clobber it.
- **Fix:** `docker --context colima-lcm-sandbox rm <name>` first, or pick a different `--name`.

### `dev-sandbox create` aborts: "REPO_PATH … is outside HOST_HOME"

- **Cause:** Colima only bind-mounts `/Users/<HOST_USER>` from macOS into the Lima VM. A `REPO_PATH` outside that tree (e.g. `/tmp/...`) silently produces an empty `/workspace`.
- **Fix:** Move the repo under your home dir, or extend the Colima profile's mounts manually.

### `docker:colima-backups` shows up in build output when you meant `lcm-sandbox`

- **Cause:** Wrong Docker context active. `docker build` uses whatever context is current, which may not be the one you intended.
- **Fix:** `docker context use colima-lcm-sandbox` before rebuilding. The launcher and the productionized scripts pin `--context` explicitly; the manual one-off `docker build` does not.

### `/dev-sandbox` skill works in this session but fails after restart with permission errors

- **Cause:** Claude Code's permission cache held a transient allow rule from earlier in the session. Restarting refreshes from disk, where only `Bash(/Users/<you>/.ai-dev-dotfiles/bin/dev-sandbox:*)` is allowlisted.
- **Fix:** This is the correct behaviour. All docker access should go through the wrapper CLI; if you need a one-off raw `docker` command, run it yourself in a real terminal.

---

## Autonomous `lcm-sandbox` flow

### `lcm-sandbox launch` exits 4 with `image not found`

- **Cause:** `lcm-hermes-agent:latest` not built locally.
- **Fix:** `bash scripts/build-hermes-image.sh` (or the manual `docker build -f scripts/Dockerfile.hermes -t lcm-hermes-agent:latest .`).

### Integration tests in `test_docker_launcher.py` show `SKIPPED`

- **Cause:** Same as above. The integration tests gate on the image being present locally — by design, so CI without the image doesn't fail.
- **Fix:** Build the image, then re-run `venv/bin/python -m pytest lcm_sandbox/tests/test_docker_launcher.py -q`.

### Container starts but the smoke test fails inside

- **Cause:** `scripts/smoke-test.sh` is gating the agent launch on UID/workspace/manifest/hook/rm-shim/CLI checks. Read the `[smoke]` `FAIL <name>` line in stderr.
- **Common sub-causes:**
    - `workspace is a real mount` — `/workspace` was not bind-mounted (probably a path-outside-HOME problem). Move the repo and retry.
    - `pre-push hook installed` — `docker-git-hooks.sh` failed to resolve the linked hooks dir for a worktree with a relative `gitdir:` line. Verify the worktree was created with `git worktree add` against a normal repo.
    - `rm-shim installed at /usr/local/bin/rm` — the image was built without the shim. Rebuild.

### `--egress-allowlist` is set but `curl https://example.com` inside the container still works

- **Cause:** Egress enforcement requires a host-side restricted bridge — `--cap-drop=ALL` blocks the container from installing its own iptables rules. The launcher's `--egress-allowlist` flag is currently advisory; it forwards the allowlist as `LCM_EGRESS_ALLOWLIST` env var inside the container, but the network policy itself is Phase 5 infrastructure work.
- **Fix:** None at the launcher level. Until Phase 5 ships, treat the flag as documentation of intent; consider running the container on a custom Docker network you've already locked down externally.

### Persona tests fail with `HTTP 500 from Privoxy` (or any local proxy)

- **Cause:** `urllib` follows the system HTTP proxy for `127.0.0.1` calls by default. The in-process `HTTPServer` fixture in `test_persona_render_capture.py` is on loopback, so the proxy intercepts it.
- **Fix:** Make sure `lcm_sandbox/tests/conftest.py` includes the autouse `NO_PROXY` fixture (added in commit `9cda868`). If you're running tests outside pytest, export `NO_PROXY=127.0.0.1,localhost,::1`.

### `lcm-sandbox cleanup` complains the worktree is missing

- **Cause:** Idempotent re-run. The command treats `already-absent` as success and exits 0; if you see an error code, the path was malformed or pointed at a non-worktree directory.
- **Fix:** Inspect the `actions` map in the JSON output — each step (`container`, `worktree`, `artifacts`) is reported independently.

### Artifact `manifest.json` reports `status: partial` with warnings

- **Cause:** One or more of the capture sub-steps (docker logs, git rev-parse, git log, git diff) returned an error but the others succeeded. The capturer is deliberately tolerant — partial results are useful.
- **Fix:** Read the `warnings` array. Common entries: container already gone (`exit code: None, container_present: False`), worktree not a git repo (`.git` missing), or `docker logs` timed out (large log volume).

---

## Build / install

### `docker build` hangs on the apt-get step

- **Cause:** Lima VM has no internet — usually a DNS or NAT problem.
- **Fix:** `colima status lcm-sandbox`. If running, `colima ssh lcm-sandbox` and `nslookup deb.nodesource.com` inside. The `setup-dev-sandbox.sh` script pre-flights this exact lookup and refuses to build if it fails.

### `pip install -e .` fails on macOS with `ld: framework not found`

- **Cause:** Xcode CLT not installed or out of date.
- **Fix:** `xcode-select --install`. Then `uv pip install --force-reinstall -e .`.

### `uv` reports a lockfile drift after pulling

- **Cause:** `pyproject.toml` changed but `uv.lock` is stale.
- **Fix:** `uv sync --upgrade` (regenerates the lockfile). Commit the updated `uv.lock`.

---

## When in doubt

- Look at the JSON output. Every CLI command emits a structured result; the `error_type`, `phase`, `step`, and `context` fields point at the exact step that failed.
- Check `~/.lcm-sandbox/artifacts/<sandbox-id>/manifest.json` — the warnings list is meant to tell you what was tolerated vs. what genuinely failed.
- Re-run with `--verbose` for debug-level structured logs on stderr.
- If the issue is reproducible, file it at the repo's issue tracker with the JSON output + the last 50 lines of `docker logs <sandbox-id>`.
