# Sandbox Image Toolchain — Design Note

**Status:** Draft, awaiting Phase 2 Dockerfile work
**Owner:** Phase 2 Dockerfile + entrypoint
**Scope:** What gets installed into the `lcm-dev-agent:latest` image so that agents inside can not only edit code but also run the consumer repo's full test suite.

---

## The decision in one sentence

**Phase 2 first cut:** ship one fat universal image. Defer per-project images until a real workload's test deps are too large or too volatile for the universal base.

---

## Why one image (not per-project)

| Concern | Universal image | Per-project image |
| :------ | :-------------- | :---------------- |
| Cold start | Slow first pull (~1–2 GB), fast every run after | Fast pull (small layers), but builds on first sandbox per project |
| Consumer-repo contract | None — consumer repo just supplies code | Consumer must maintain `.sandbox/Dockerfile` |
| Test-dep drift | Universal image rebuilt monthly; per-repo deps installed at run-time via repo's own `pip install -r ...` | Image tracks repo deps exactly |
| Failure mode | One image breakage hits every consumer | One repo's Dockerfile mistake breaks only that repo |
| Maintenance | One image to keep current | N images |

Universal wins for the first cut because:
1. We don't yet have N consumers — we have ~1–3.
2. The image's job is to provide *language runtimes and binaries*, not pinned project deps. Project deps get installed inside the sandbox at run time via `pip install -r requirements.txt` etc.
3. We can pivot to per-project later by introducing a `--image-tag` override on the host CLI without breaking the universal flow.

---

## What goes into the universal image

### Base
- `FROM ubuntu:24.04` (per existing SANDBOX-DETAILED-FLOW.md). LTS, predictable, broad package availability.

### System tooling (apt-get)
- `git`, `git-lfs`, `ssh-client` (read-only remote ops, push blocked by hook)
- `curl`, `wget`, `ca-certificates`
- `jq`, `yq`
- `build-essential`, `pkg-config`, `cmake` (needed for many Python wheels and Rust deps)
- `libssl-dev`, `libffi-dev`, `zlib1g-dev`, `libsqlite3-dev` (common native bindings)
- `python3` + headers, `python3-pip`, `python3-venv`
- `vim-tiny`, `less`, `tree` (debugging ergonomics; tiny footprint)
- `sudo` is **NOT** installed (aiagent is non-root)
- `tzdata`, `locales` (UTC + en_US.UTF-8 default)

### Language runtimes
- **Python 3.12** (Ubuntu 24.04 default) + `pip`, `setuptools`, `wheel`
- **Node.js LTS** (currently 22.x) + `npm`, `corepack` (yarn/pnpm available on demand)
- *(Future, only when a consumer needs it)*: Go, Rust, Java. Add via separate overlay images, do not pre-install in the universal base.

### Python test toolchain (system-wide)
- `pytest`, `pytest-cov`, `pytest-mock`, `pytest-xdist`
- `pytest-asyncio` (covers async test patterns)
- `coverage[toml]`
- `pip-tools` (for repos that pin deps via pip-compile)

### Node test toolchain
- Whatever ships with Node LTS (`node --test`).
- `npx` is enough to bring in per-repo test runners (jest, vitest, mocha) at run time.

### Agent CLIs
- `@anthropic-ai/claude-code` (latest) — `npm install -g`
- `codex` — install method TBD; check Phase 2.
- `gemini` (Google's CLI) — install method TBD; check Phase 2.

### Sandbox-specific
- `/opt/lcm-sandbox/` — directory containing:
  - `apply_agent_profile.py` (renders the in-sandbox agent configs)
  - `agent_profiles/permissive.json`, `agent_profiles/standard.json`
  - `entrypoint.sh`
  - `git-hooks/pre-push`

### User
- `aiagent:agentgroup` (non-root, no sudo). Home directory `/home/aiagent`. Default shell `/bin/bash`. PATH includes `/usr/local/bin`, `/usr/bin`, `/home/aiagent/.local/bin`.

### Excluded on purpose
- **Browser engines / Playwright.** Adds ~500 MB and conflicts with most container security postures. Repos that need E2E browser tests should use a separate `lcm-dev-agent-e2e:latest` overlay or bring their own image via `--image-tag`.
- **Database servers.** Tests should connect to ephemeral services on the host or use SQLite in-memory.
- **Docker-in-Docker.** Agents inside the sandbox should not spawn nested containers — adds attack surface and creates ACL holes.
- **Compilers for languages no consumer uses yet.** Add when needed, not speculatively.

---

## Image size budget

| Layer | Estimated size | Notes |
| :---- | :------------- | :---- |
| ubuntu:24.04 base | ~80 MB | Required |
| System tooling + build-essential | ~400 MB | Mostly build-essential |
| Python 3.12 + pip tooling | ~150 MB | |
| Node LTS + npm | ~200 MB | |
| Agent CLIs (claude, codex, gemini) | ~200 MB | npm-based; TBD for the others |
| Sandbox-specific scripts | ~5 MB | |
| **Total target** | **~1 GB** | |

**Hard ceiling:** 1.5 GB. If we exceed it, split into base + overlay.

---

## Image build & versioning

- Tag scheme: `lcm-dev-agent:<semver>` plus `:latest` alias.
- Source of truth: `docker/Dockerfile` in this repo. Build context: `docker/`.
- CI builds on every merge to main. Pushes to a registry TBD (Phase 2 question).
- The `lcm-sandbox create` CLI's `--image-tag` option defaults to `lcm-dev-agent:latest` and accepts any pinned version for reproducibility.

---

## Run-time dependency installation inside the sandbox

The agent's first-class workflow includes installing the consumer repo's own deps:

```bash
# Inside the container, on `aiagent` user:
cd /workspace
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt        # or pyproject.toml, or pip-tools
npm install                            # if package.json present
```

This is **not** baked into the entrypoint. The agent is expected to do it as the first step of its plan, because:
- It's where most repo-specific failures surface — surface them in the agent's audit trail, not in our entrypoint logs.
- Some plans don't need full deps (docs-only changes); don't pay the cost.
- Cached deps from previous worktree reuse will still be on disk; agent can skip install when appropriate.

---

## Verification (STEP 3.2)

After build, the image is verified by running:

```bash
docker run --rm lcm-dev-agent:latest /bin/bash -c '
  set -e
  git --version
  python3 --version
  pip --version
  node --version
  npm --version
  claude --version || echo "claude missing"
  codex --version || echo "codex missing"
  gemini --version || echo "gemini missing"
  jq --version
'
```

All commands must exit 0 and produce non-empty output. Missing agent CLIs are warnings in Phase 1's loose verification, errors in Phase 2's strict verification.

---

## Open questions for Phase 2 (decide before Dockerfile lands)

1. Confirm canonical install commands for `codex` and `gemini` CLIs. (Until then they're stubbed.)
2. Decide whether `node` should be the LTS bundled in Ubuntu's apt or installed via NodeSource for a more recent version. Recommendation: NodeSource for currency.
3. Decide registry: Docker Hub, GHCR, internal registry, or local-only for the first cut? Recommendation: local-only until we have a CI pipeline.
4. Decide on a base image lockfile / SBOM strategy. Recommendation: defer until Phase 4.
5. Confirm whether arm64 (Apple Silicon) and amd64 multi-arch builds are needed from day 1. Recommendation: yes — buildx with both arches, since the host dev fleet is Apple Silicon.

---

## What this design is NOT

- Not a final Dockerfile. The Dockerfile is a Phase 2 deliverable.
- Not a list of every transitive dep. apt and npm handle those.
- Not a commitment to never split the image. We will split if size or update cadence demands it.

---

## Control plane integration (added 2026-06-19; tracks aidevops TODO #112)

The agentic sandbox control plane design (`SANDBOX-CONTROL-PLANE.html`, `SANDBOX-CONTROL-SCHEMA.md`) requires three image-level capabilities. The current `Dockerfile.hermes` partially provides them but with **one significant naming question that must be resolved before further changes**.

### Naming clarification (2026-06-19)

**Resolved:** "Hermes" throughout this codebase means **NousResearch's Hermes Agent**, installed in `Dockerfile.hermes` at line 66 via `install.sh` from `NousResearch/hermes-agent`. There is no separate "Platform Hermes."

The aidevops uncommitted work in `server/modules/llm-routing/`, `server/modules/personas/`, `tools/llm_router/`, `tools/policy/` is the platform's integration of this same Hermes — defining personas, configuring routing, and enforcing classification policy via Hermes skills/config. It is not a separate Hermes implementation.

This means:
- The Hermes that runs inside `lcm-hermes-agent:latest` is the one the control plane design refers to.
- Classification policy enforcement happens **inside Hermes** via skills/config, not by a separate component.
- "AIDevOps talks to Hermes" means AIDevOps talks to the in-container Hermes runtime (via the Phase A `/control/` filesystem or future Phase B MCP).
- No image-level naming change needed.

### What has been done in the image (2026-06-19, commit landing this section)

1. **`lcm-sandbox` Python package is installed** in the image (`COPY pyproject.toml + lcm_sandbox/` into `/tmp/lcm-sandbox-src` then `pip install`). This makes the `sandbox-emit` CLI available on PATH for the in-container agent and for Hermes skills that wrap it.

2. **`CONTROL_DIR=/control` env var is set** in the image so any actor inside (agent direct, sandbox-emit, Hermes skills) uses the same convention.

3. **`/control` directory is pre-created** with `aiagent:agentgroup` ownership so the bind mount lands on the right perms at docker run time.

### What still needs work (next sessions)

1. **`entrypoint.sh` integration with the control plane.** The current entrypoint loads the persona and launches Hermes. It needs to additionally:
   - On startup: read `/control/plan/plan.json`, emit `launched` and `plan_loaded` events via `sandbox-emit event ...`.
   - Start a long-lived `sandbox-emit heartbeat-daemon` background process so the run does not appear stalled.
   - On Hermes exit: emit `tasks_complete` or `failed` event with exit code; update status phase accordingly.
   - On SIGTERM: emit `failed` event with `reason=cancelled` and clean shutdown.

2. **Hermes-side classification policy and ask/answer wiring.** Hermes skills or config that:
   - Apply data classification policy on outbound LLM calls (consumes `aidevops/design/LLM-DATA-CLASSIFICATION-POLICY.md`).
   - Convert "I need operator input" intent into outbox `ask` messages via `sandbox-emit ask`.
   - Surface inbox answers back to Hermes as tool results.

   These belong to the in-flight aidevops `server/modules/personas/` and `tools/llm_router/` work; this image just hosts the result.

3. **HTTP endpoint at `localhost:8765`** (Phase B prep). Not needed for Phase A (filesystem-only). When the MCP transport lands per `SANDBOX-ORCHESTRATION.md`, Hermes will expose this endpoint and AIDevOps will switch from filesystem polling to live MCP. Schemas (`SANDBOX-CONTROL-SCHEMA.md`) are already designed to carry the same shapes across both transports.

### Cross-references

- `SANDBOX-CONTROL-PLANE.html` — design + flows
- `SANDBOX-CONTROL-SCHEMA.md` — wire-format contracts that Platform Hermes must implement
- `lcm_sandbox/core/control_plane.py` — Python writers used by `sandbox-emit` (and by Platform Hermes when it lands)
- `lcm_sandbox/cli_emit.py` — the `sandbox-emit` CLI entry point
- aidevops TODO #112 (this work item)
- aidevops uncommitted: `server/modules/llm-routing/`, `tools/llm_router/`, `tools/policy/`, `server/modules/personas/` — where Platform Hermes is currently being built
