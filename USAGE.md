# LCM-Sandbox — Usage Guide

**File:** `USAGE.md`
**Description:** End-user-facing quickstart for both sandbox flows shipped by this repo: the autonomous `lcm-sandbox` Python CLI and the manual `dev-sandbox` host-CLI wrapper. Designed to be read in order; readers do not need to consult the design docs to follow it.
**Author:** Libor Ballaty <libor@arionetworks.com>
**Created:** 2026-06-17
**Last Updated:** 2026-06-17
**Last Updated By:** Libor Ballaty

---

## Which flow do I want?

| Goal | Use |
| :--- | :--- |
| I want to drop into a shell inside a sandbox container and work on a repo by hand (with Claude / Codex / Gemini CLI inside) | **Manual `dev-sandbox`** flow (§3) |
| I want an automated agent run against an approved plan + worktree, with artifact capture | **Autonomous `lcm-sandbox`** flow (§2) |
| I'm orchestrating from AIDevOps and need MCP back-channel | Same as autonomous, plus Phase 5 wiring (out of scope here — see `SANDBOX-ORCHESTRATION.md`) |

The two flows are deliberately separate: the manual flow is for human interactive use with a single dotfile-mounted container; the autonomous flow is for short-lived agent runs that produce committed artifacts.

---

## 1. Prerequisites

### Required on the host

- macOS or Linux. (Tested on macOS Sequoia with Colima.)
- [Colima](https://github.com/abiosoft/colima) (or another Docker context) capable of running ARM64 + AMD64 images.
- `docker` CLI on `PATH`.
- Python ≥ 3.11 with [`uv`](https://docs.astral.sh/uv/) or a venv.
- Git ≥ 2.30.
- `jq` (for entrypoint manifest writing).

### Colima profile (for the manual flow)

One-time setup of the dedicated profile:

```bash
colima start lcm-sandbox \
  --cpu 4 --memory 8 --disk 60 \
  --arch aarch64 --runtime docker
```

The autonomous flow can use any running Docker context.

### Install `lcm-sandbox` (autonomous CLI)

From a checkout:

```bash
cd /Users/<you>/LocalProjects/GitHubProjectsDocuments/LCM-Sandbox
uv venv venv
source venv/bin/activate
uv pip install -e .
lcm-sandbox --version
```

---

## 2. Autonomous flow (`lcm-sandbox`)

Full lifecycle: `create` → `launch` → `cleanup`. Each step writes a JSON result to stdout and uses dedicated exit codes (`1=preflight`, `2=worktree`, `3=sync`, `4=docker-image`, `5=launch`, `0=ok`).

### 2.1 Create the worktree

```bash
lcm-sandbox create \
  --repo /path/to/repo \
  --branch feature/agent-run \
  --allowed-paths '{"write":["src/","tests/"],"read":["*"]}' \
  --timeout 60 \
  --colima-profile lcm-dev
```

Output (truncated):

```json
{
  "sandbox_id": "sandbox-run_abc123-...",
  "status": "ready_for_docker_launch",
  "worktree_path": "/path/to/repo-sandboxes/sandbox-run_abc123-...",
  "branch": "feature/agent-run",
  "latest_commit": "deadbeef...",
  "phase": 3,
  "next_step": "launch"
}
```

Pick the `sandbox_id` and `worktree_path` out — every later command needs them.

### 2.2 Launch the container

```bash
lcm-sandbox launch \
  --sandbox-id sandbox-run_abc123-... \
  --worktree-path /path/to/repo-sandboxes/sandbox-run_abc123-... \
  --branch feature/agent-run \
  --allowed-paths '{"write":["src/","tests/"],"read":["*"]}' \
  --timeout 60
```

The image used is `lcm-hermes-agent:latest`. Build it first with `bash scripts/build-hermes-image.sh` if you haven't.

Hermes persona variant — adds the persona renderer and gateway:

```bash
lcm-sandbox launch \
  --sandbox-id sandbox-run_abc123-... \
  --worktree-path /path/.../sandbox-run_abc123-... \
  --hermes-persona config-auditor \
  --mcp-url http://host.docker.internal:9700/mcp \
  --mcp-token "$(cat /path/to/per-run-token.txt)" \
  --model-provider anthropic \
  --model-key claude-opus-4-7 \
  --egress-allowlist api.anthropic.com:443,host.docker.internal:9700
```

The `--egress-allowlist` flag is advisory until host-side enforcement lands (Phase 5); the value is forwarded as `LCM_EGRESS_ALLOWLIST` env var inside the container so an entrypoint or sidecar can install rules where the network driver supports them.

### 2.3 Inspect status

```bash
lcm-sandbox status --sandbox-id sandbox-run_abc123-...
```

### 2.4 Stop (optionally keep the container for post-mortem)

```bash
lcm-sandbox stop --sandbox-id sandbox-run_abc123-...
lcm-sandbox stop --sandbox-id sandbox-run_abc123-... --keep
```

### 2.5 Cleanup (capture + teardown)

```bash
lcm-sandbox cleanup \
  --sandbox-id sandbox-run_abc123-... \
  --worktree-path /path/to/repo-sandboxes/sandbox-run_abc123-... \
  --branch feature/agent-run \
  --allowed-paths '{"write":["src/","tests/"],"read":["*"]}'
```

Idempotent — safe to re-run. By default the artifact directory under `~/.lcm-sandbox/artifacts/<sandbox-id>/` is preserved; pass `--remove-artifacts` to wipe it. Pass `--keep-worktree` to leave the host worktree on disk for inspection.

Output:

```json
{
  "sandbox_id": "sandbox-run_abc123-...",
  "actions": {
    "container": "removed",
    "worktree": "removed",
    "artifacts": "kept at /Users/.../artifacts/sandbox-run_abc123-..."
  },
  "cleaned_at": "2026-06-17T17:42:11.123456+00:00"
}
```

### 2.6 Where artifacts live

`~/.lcm-sandbox/artifacts/<sandbox-id>/`:

- `manifest.json` — capture summary (commit count, container exit code, warnings).
- `commits.json` — new commits since the worktree baseline.
- `diff.patch` — `git diff <baseline>..HEAD`.
- `stdout.log` / `stderr.log` — split container logs (when supported).
- `agent.log` — combined fallback of both streams.

---

## 3. Manual flow (`dev-sandbox`)

A single host CLI (`~/.ai-dev-dotfiles/bin/dev-sandbox`) wraps build / run / verify / stop. It is the only command needed for the manual flow and is intended to be the *only* command an agent needs allowlisted (`Bash(/Users/<you>/.ai-dev-dotfiles/bin/dev-sandbox:*)`).

### 3.1 Create + enter a sandbox

```bash
dev-sandbox create /path/to/repo --name my-sandbox
```

Defaults: `/path/to/repo = $PWD` and `--name = lcm-sandbox-<basename>`. The CLI auto-runs the 11-check verification suite (claude / codex / gemini versions, git identity, `/workspace` r/w, dotfile mounts) and refuses if anything fails.

Enter the running container (real terminal — `docker exec -it` needs a TTY):

```bash
docker --context colima-lcm-sandbox exec -it my-sandbox bash
```

Or have the CLI print the exact command:

```bash
dev-sandbox enter my-sandbox
```

### 3.2 Add extra mounts

```bash
dev-sandbox create /path/to/repo \
  --mount /tmp/data:/data:ro \
  --mount /Users/<you>/.shared-config:/home/aiagent/.shared-config:ro
```

The repo must be under `$HOME` because Colima only bind-mounts `/Users/<you>` into the Lima VM by default. Paths outside that tree silently produce empty mounts; the wrapper rejects them with a clear error.

### 3.3 Lifecycle

```bash
dev-sandbox list                    # show managed sandboxes
dev-sandbox verify my-sandbox       # re-run the 11-check suite
dev-sandbox restart my-sandbox      # restart + re-verify
dev-sandbox stop my-sandbox         # safe stop + rm (image+label guarded)
```

### 3.4 Autonomous Claude inside the manual sandbox

The container itself is the isolation boundary, so skip all in-Claude prompts:

```bash
claude --dangerously-skip-permissions
```

Or persist per-repo by writing `<repo>/.claude/settings.local.json`:

```bash
mkdir -p /workspace/.claude
cat > /workspace/.claude/settings.local.json <<'EOF'
{"defaultMode": "bypassPermissions"}
EOF
```

---

## 4. Where to look next

- Common gotchas → `TROUBLESHOOTING.md`.
- Manual-flow internals (image build, mount table, runbook) → `scripts/README-dev-sandbox.md`.
- Autonomous flow design → `SANDBOX-ARCHITECTURE.md` + `SANDBOX-DETAILED-FLOW.md`.
- In-sandbox agent permission profiles → `SANDBOX-AGENT-CONFIG.md`.
- Phase 5 MCP back-channel design → `SANDBOX-ORCHESTRATION.md`.
- WP-8 HERMES persona contract → `HERMES-PERSONA-INTEGRATION-PLAN.md` (pointer) + the canonical doc it references.
- Verified Phase 5 prereqs → `docs/PHASE-5-PREREQS-VERIFICATION.md`.
