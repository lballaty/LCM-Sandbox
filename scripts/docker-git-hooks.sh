#!/bin/bash
# File: LCM-Sandbox/scripts/docker-git-hooks.sh
# Description: Installs the pre-push deny hook into the workspace's git hooks
#              directory inside an lcm-hermes-agent / lcm-dev-agent container.
#              Handles both normal repos (.git is a directory) and worktrees
#              (.git is a file containing `gitdir: <linked-hooks-dir>`). This is
#              one of the layered defenses required by SANDBOX-AGENT-CONFIG.md
#              §4 (container ACLs + git hook) so that even if egress is open,
#              commits cannot be pushed to origin.
# Author: Libor Ballaty <libor@arionetworks.com>
# Created: 2026-06-16
#
# Exit code: 0 on success or "no .git found" (logged); non-zero only on a
# verifiable installation failure (write denied to the hooks dir).
#
# Usage: docker-git-hooks.sh [WORKSPACE_DIR]    (default WORKSPACE_DIR=/workspace)

set -euo pipefail

log() { printf '[git-hooks] %s\n' "$*" >&2; }

WORKSPACE="${1:-/workspace}"

if [[ ! -e "${WORKSPACE}/.git" ]]; then
  log "no .git entry at ${WORKSPACE}/.git — skipping hook install"
  exit 0
fi

# Resolve the actual hooks directory.
# - Normal repo: ${WORKSPACE}/.git/hooks
# - Worktree:    parse the `gitdir:` line from the .git file, which points at
#                <parent>/.git/worktrees/<name>; hooks live in that dir.
if [[ -d "${WORKSPACE}/.git" ]]; then
  HOOKS_DIR="${WORKSPACE}/.git/hooks"
elif [[ -f "${WORKSPACE}/.git" ]]; then
  gitdir_line=$(grep -E '^gitdir:' "${WORKSPACE}/.git" | head -1 || true)
  if [[ -z "${gitdir_line}" ]]; then
    log "FATAL: ${WORKSPACE}/.git is a file but has no gitdir: line"
    exit 1
  fi
  linked_dir="${gitdir_line#gitdir: }"
  linked_dir="${linked_dir#gitdir:}"
  linked_dir="${linked_dir//[$'\t\r\n ']/}"
  # gitdir is sometimes relative to WORKSPACE (e.g. `gitdir: ../.git/worktrees/foo`)
  if [[ "${linked_dir}" != /* ]]; then
    linked_dir="${WORKSPACE}/${linked_dir}"
  fi
  HOOKS_DIR="${linked_dir}/hooks"
else
  log "FATAL: ${WORKSPACE}/.git is neither file nor directory"
  exit 1
fi

mkdir -p "${HOOKS_DIR}"

cat > "${HOOKS_DIR}/pre-push" <<'HOOK'
#!/bin/bash
# Installed by docker-git-hooks.sh. Blocks all pushes from inside the sandbox.
# Commits are allowed; pushes to origin are not. See SANDBOX-AGENT-CONFIG.md §4.
echo "ERROR: pushes are blocked from inside the sandbox." >&2
echo "       Commits are captured by the host-side artifact-capture flow." >&2
exit 1
HOOK

chmod +x "${HOOKS_DIR}/pre-push"
log "pre-push hook installed at ${HOOKS_DIR}/pre-push"
