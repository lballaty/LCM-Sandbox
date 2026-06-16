# Manual Dev Sandbox — Procedure

**File:** `scripts/README-dev-sandbox.md`
**Description:** End-to-end procedure for creating, entering, and tearing down a manual dev-sandbox container. Distinct from the autonomous `lcm-sandbox` Python CLI (which targets agentic per-plan sandboxes). This is the hand-driven workflow for when a human wants Claude Code + Codex CLI in a Docker container with a repo + host dotfiles mounted.
**Author:** Libor Ballaty <libor@arionetworks.com>
**Created:** 2026-06-15
**Last Updated:** 2026-06-16
**Last Updated By:** Libor Ballaty
**Verified end-to-end:** 2026-06-16 (all 11 verify checks PASS — claude, codex, gemini, node, python3, git identity, /workspace ls + rw, .claude / .codex / .ai-dev-dotfiles mounts)

---

## Components

| Artifact | Purpose |
| :--- | :--- |
| `scripts/Dockerfile.dev-sandbox` | Image definition. Ubuntu 24.04 + Node 20 + Python + uv + Claude Code + Codex CLI + Gemini CLI. Each CLI installs in its own RUN so a single-package failure fails the build instead of being masked. Adds `/Users/<HOST_USER>` → `/home/aiagent` symlink so host-absolute paths in dotfiles resolve. |
| `scripts/setup-dev-sandbox.sh` | Idempotent setup. Verifies colima profile, pins context, builds image if missing. Pre-flights VM DNS to `deb.nodesource.com`. No CLI arguments — env vars only (`LCM_FORCE_REBUILD=1` for force-rebuild). |
| `scripts/run-dev-sandbox.sh` | Launcher. Wraps `docker run` with pinned `--context colima-lcm-sandbox`, adds `--label lcm-dev-sandbox=managed` for ownership tracking, rejects REPO_PATH outside `$HOST_HOME` (colima only bind-mounts that prefix). Env-overridable. |
| `scripts/verify-dev-sandbox.sh` | In-container verification suite. Checks CLI versions, git identity, `/workspace` r/w, dotfile mounts. Exits non-zero on any fail. Usage: `verify-dev-sandbox.sh <container-name>`. |
| `scripts/stop-dev-sandbox.sh` | Teardown. Verifies image and `lcm-dev-sandbox=managed` label before destroying so it cannot accidentally remove unrelated containers. Usage: `stop-dev-sandbox.sh <container-name>`. |
| `~/.ai-dev-dotfiles/bin/dev-sandbox` | Host CLI wrapper. Single entry-point with subcommands: `create`, `verify`, `restart`, `stop`, `list`, `enter`, `help`. Intended to be the only command allowlisted for agentic use. |
| Colima profile `lcm-sandbox` | Dedicated VM: 4 CPU / 8 GiB / 60 GiB, aarch64, docker runtime. |

---

## One-time setup (already done — keep for reference)

```bash
# Colima profile
colima start lcm-sandbox --cpu 4 --memory 8 --disk 60 --arch aarch64 --runtime docker

# Build the image (run from repo root)
docker build -f scripts/Dockerfile.dev-sandbox -t lcm-dev-sandbox:latest .
```

Switch back to this profile any time with:
```bash
docker context use colima-lcm-sandbox
```

---

## Creating a new sandbox

### Recommended — via the host CLI wrapper

From any repo on this device:

```bash
dev-sandbox create                                # current directory as REPO_PATH
dev-sandbox create /path/to/repo --name my-sb    # explicit path + name
dev-sandbox create --mount /tmp/data:/data:ro    # add extra mounts
dev-sandbox create --rebuild                     # force image rebuild
dev-sandbox list                                  # show managed sandboxes
dev-sandbox verify my-sb                          # re-run verification
dev-sandbox restart my-sb                         # restart + re-verify
dev-sandbox stop my-sb                            # safe stop + remove
dev-sandbox enter my-sb                           # prints docker exec command for a real TTY
```

REPO_PATH must be under `$HOME` (Colima only mounts `/Users/<HOST_USER>` into the VM). The wrapper auto-verifies after `create`.

### Direct script invocation (lower level)

```bash
# Idempotent setup (build image if missing)
scripts/setup-dev-sandbox.sh

# Launch
REPO_PATH=/Users/liborballaty/LocalProjects/GitHubProjectsDocuments/<repo> \
CONTAINER_NAME=lcm-sandbox-<repo> \
scripts/run-dev-sandbox.sh

# Verify / stop
scripts/verify-dev-sandbox.sh lcm-sandbox-<repo>
scripts/stop-dev-sandbox.sh   lcm-sandbox-<repo>
```

### What the launcher mounts

| Host path | Container path | Mode | Why |
| :--- | :--- | :--- | :--- |
| `<REPO_PATH>` | `/workspace` | rw | The repo you're working on. |
| `~/.claude` | `/home/aiagent/.claude` | rw | OAuth tokens refreshed by `/login` must persist. |
| `~/.codex` | `/home/aiagent/.codex` | rw | Codex needs to write PATH-alias files on first run. |
| `~/.gemini` | `/home/aiagent/.gemini` | rw | Gemini CLI persists OAuth / API key state. |
| `~/.ai-dev-dotfiles` | `/home/aiagent/.ai-dev-dotfiles` | ro | Shared agent config; container should not mutate. |
| `~/.gitconfig` | `/home/aiagent/.gitconfig` | ro | Identity only. |

> `~/.ssh` is intentionally **not** mounted — sandbox cannot push.

---

## Entering the sandbox

Use a **real terminal** (not Claude's `!` bash prompt — that has no TTY):

```bash
docker exec -it <container-name> bash
```

You land in `/workspace` as user `aiagent` (UID 1000, NOPASSWD sudo).

### First-session verification

```bash
claude --version          # 2.1.177 (or newer)
codex --version           # 0.139.0 (or newer)
ls -la ~/.claude          # token files present
ls -la ~/.ai-dev-dotfiles # README.md, tools/, etc.
git -C /workspace status  # confirms repo mount
```

### Autonomous Claude

The sandbox itself is the isolation boundary, so skip all in-Claude prompts:

```bash
claude --dangerously-skip-permissions
# or, persist per-repo (writes to <repo>/.claude/settings.local.json):
mkdir -p /workspace/.claude
cat > /workspace/.claude/settings.local.json <<'EOF'
{"defaultMode": "bypassPermissions"}
EOF
```

---

## Stop / restart / remove

```bash
docker stop  <container-name>
docker start <container-name>
docker rm    <container-name>      # only after stop
```

The image (`lcm-dev-sandbox:latest`) survives. Removing a container loses anything written outside `/workspace` (e.g. shell history, `~/.cache`).

---

## Rebuilding the image

After editing `Dockerfile.dev-sandbox`:

```bash
docker build -f scripts/Dockerfile.dev-sandbox -t lcm-dev-sandbox:latest .
```

Existing containers keep running on the old image until recreated.

---

## Common gotchas

1. **`docker exec -it ... bash` fails with "not a TTY".** You ran it through the Claude harness's `!` shell. Open a real terminal.
2. **`/login` doesn't persist.** `~/.claude` is mounted ro. Recreate the container with the launcher (it mounts rw).
3. **Hook script "No such file or directory" for `/Users/liborballaty/.claude/hooks/...`.** Image was built before the symlink was added. Rebuild.
4. **Codex "Read-only file system (os error 30)" warning.** Same as #2 — recreate with rw `.codex` mount.
5. **Container won't start, name conflict.** `docker rm <name>` first; the launcher refuses to clobber an existing container.
6. **Colima context wrong.** `docker context use colima-lcm-sandbox`. The launcher uses whatever context is active.

---

## Future improvements (not blocking)

- Bake the autonomous-mode default into the image's `/home/aiagent/.claude/settings.json` (currently relies on the host file being rw-mounted).
- Add a `--name` / `--repo` flag to the launcher instead of env vars (purely cosmetic).
- Optional `~/.ssh` mount for repos that need to push — gated behind an explicit flag.
- Consider mounting `~/.ai-dev-dotfiles` rw if you want the container to update shared configs (currently ro by design).
