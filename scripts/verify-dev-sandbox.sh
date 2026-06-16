#!/usr/bin/env bash
# File: LCM-Sandbox/scripts/verify-dev-sandbox.sh
# Description: Run a fixed verification suite inside a dev-sandbox container.
#              Checks agent CLI versions, git identity, /workspace mount,
#              and dotfile mounts. Emits PASS/FAIL per check and exits
#              non-zero if any check failed. Pinned to the colima-lcm-sandbox
#              docker context.
# Author: Libor Ballaty <libor@arionetworks.com>
# Created: 2026-06-16
# Usage:
#   scripts/verify-dev-sandbox.sh <container-name>

set -euo pipefail

PROFILE="lcm-sandbox"
CONTEXT="colima-${PROFILE}"
NAME="${1:-}"
[[ -n "$NAME" ]] || { echo "usage: $0 <container-name>" >&2; exit 2; }

fail=0
check() {
  local label="$1"; shift
  if out="$(docker --context "$CONTEXT" exec "$NAME" "$@" 2>&1)"; then
    printf '  PASS  %-28s  %s\n' "$label" "$(printf '%s' "$out" | head -1)"
  else
    printf '  FAIL  %-28s  %s\n' "$label" "$(printf '%s' "$out" | head -1)"
    fail=1
  fi
}

# Container must exist and be running.
state="$(docker --context "$CONTEXT" inspect --format '{{.State.Status}}' "$NAME" 2>/dev/null || true)"
if [[ "$state" != "running" ]]; then
  echo "container '$NAME' is not running (state=${state:-missing}) on $CONTEXT" >&2
  exit 3
fi

echo "verifying container '$NAME' on $CONTEXT"

check "claude --version"    claude --version
check "codex --version"     codex --version
check "gemini --version"    gemini --version
check "node --version"      node --version
check "python3 --version"   python3 --version
check "git user.email"      git config --global --get user.email
check "/workspace ls"       ls /workspace
check ".claude mount"       ls /home/aiagent/.claude
check ".codex mount"        ls /home/aiagent/.codex
check ".ai-dev-dotfiles"    ls /home/aiagent/.ai-dev-dotfiles

# Workspace write test (writes inside container; host path follows the mount).
marker="/workspace/.lcm-dev-sandbox-verify-$$"
if docker --context "$CONTEXT" exec "$NAME" bash -c "echo ok > '$marker' && cat '$marker' && rm '$marker'" >/dev/null 2>&1; then
  printf '  PASS  %-28s  %s\n' "/workspace rw" "wrote+read+deleted marker"
else
  printf '  FAIL  %-28s  %s\n' "/workspace rw" "could not write under /workspace"
  fail=1
fi

if [[ "$fail" -eq 0 ]]; then
  echo "verify: ALL PASS"
  exit 0
else
  echo "verify: FAILED" >&2
  exit 1
fi
