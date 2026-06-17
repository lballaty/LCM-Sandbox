#!/bin/bash
# File: LCM-Sandbox/scripts/smoke-test.sh
# Description: Bootstrap smoke test for the lcm-hermes-agent container. Runs as
#              part of the entrypoint AFTER permission setup and BEFORE the
#              agent is launched. Refuses to drop to the agent if any check
#              fails. Catches: missing dotfiles, wrong UID, missing git hooks,
#              broken /workspace mount, missing CLIs.
#              Required by SANDBOX-AGENT-CONFIG.md §4 "Bootstrap smoke test".
# Author: Libor Ballaty <libor@arionetworks.com>
# Created: 2026-06-16
#
# Exit codes: 0 if all checks pass; non-zero with the failed check name in
# stderr on first failure.
#
# Usage: smoke-test.sh [WORKSPACE_DIR]    (default /workspace)

set -uo pipefail

WORKSPACE="${1:-/workspace}"
FAIL=0

log()  { printf '[smoke] %s\n' "$*" >&2; }
pass() { printf '[smoke]   PASS %s\n' "$*" >&2; }
fail() { printf '[smoke]   FAIL %s\n' "$*" >&2; FAIL=1; }

check() {
  local name="$1"; shift
  if "$@" >/dev/null 2>&1; then
    pass "${name}"
  else
    fail "${name}"
  fi
}

log "starting bootstrap smoke test"

# ---------------------------------------------------------------------------
# Process-context checks
# ---------------------------------------------------------------------------
check "running as root (entrypoint privilege)" test "$(id -u)" -eq 0
check "aiagent user exists"                   id aiagent
check "aiagent UID is 1000"                   sh -c 'test "$(id -u aiagent)" -eq 1000'

# ---------------------------------------------------------------------------
# Workspace mount + manifest
# ---------------------------------------------------------------------------
check "/workspace exists"                      test -d "${WORKSPACE}"
check "/workspace is a real mount"             sh -c "mountpoint -q ${WORKSPACE} || test -d ${WORKSPACE}"
check "/workspace/.git present"                test -e "${WORKSPACE}/.git"
check ".sandbox-manifest.json written"         test -f "${WORKSPACE}/.sandbox-manifest.json"

# ---------------------------------------------------------------------------
# Git pre-push hook
# ---------------------------------------------------------------------------
# Hook may be in either /workspace/.git/hooks or a linked worktree hooks dir.
HOOK_FOUND=0
if [[ -f "${WORKSPACE}/.git/hooks/pre-push" ]]; then
  HOOK_FOUND=1
elif [[ -f "${WORKSPACE}/.git" ]]; then
  gitdir_line=$(grep -E '^gitdir:' "${WORKSPACE}/.git" | head -1 || true)
  linked="${gitdir_line#gitdir: }"
  linked="${linked//[$'\t\r\n ']/}"
  if [[ "${linked}" != /* ]]; then
    linked="${WORKSPACE}/${linked}"
  fi
  [[ -f "${linked}/hooks/pre-push" ]] && HOOK_FOUND=1
fi
if [[ "${HOOK_FOUND}" -eq 1 ]]; then
  pass "pre-push hook installed"
else
  fail "pre-push hook installed"
fi

# ---------------------------------------------------------------------------
# rm-shim
# ---------------------------------------------------------------------------
check "rm-shim installed at /usr/local/bin/rm" test -x /usr/local/bin/rm
check "rm resolves to the shim"                sh -c 'command -v rm | grep -q "^/usr/local/bin/rm$"'

# ---------------------------------------------------------------------------
# Agent CLI presence (allow any of claude / codex / gemini — at least one)
# ---------------------------------------------------------------------------
CLI_FOUND=0
for cli in claude codex gemini; do
  if command -v "${cli}" >/dev/null 2>&1; then
    CLI_FOUND=1
  fi
done
if [[ "${CLI_FOUND}" -eq 1 ]]; then
  pass "at least one agent CLI is on PATH"
else
  fail "at least one agent CLI is on PATH"
fi

# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------
if [[ "${FAIL}" -eq 0 ]]; then
  log "all smoke-test checks PASSED"
  exit 0
fi
log "one or more checks FAILED — refusing to launch agent"
exit 1
