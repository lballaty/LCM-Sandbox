#!/bin/bash
# File: LCM-Sandbox/scripts/entrypoint.sh
# Description: In-container entrypoint for the lcm-hermes-agent image. Implements
#              SANDBOX-DETAILED-FLOW.md STEP 4.5.1-4.5.10, extended with Hermes
#              persona render + gateway start when HERMES_PERSONA is set.
# Author: Libor Ballaty <libor@arionetworks.com>
# Created: 2026-06-12
#
# Runs as root (set by the launcher; image USER is overridden). The script chmods
# and chowns the bind-mounted /workspace, then drops privileges to aiagent before
# starting Hermes or an interactive shell.

set -euo pipefail

log() { printf '[entrypoint] %s\n' "$*" >&2; }
die() { log "FATAL: $*"; exit 1; }

# ---------------------------------------------------------------------------
# 4.5.1: Parse and validate env vars
# ---------------------------------------------------------------------------
: "${PLAN_ID:?PLAN_ID is required}"
: "${RUN_ID:?RUN_ID is required}"
: "${SANDBOX_ID:?SANDBOX_ID is required}"
: "${ALLOWED_PATHS:?ALLOWED_PATHS is required}"
: "${TIMEOUT_MINUTES:=60}"
: "${BRANCH_NAME:=unknown}"

# Validate ALLOWED_PATHS is JSON.
if ! echo "${ALLOWED_PATHS}" | jq empty >/dev/null 2>&1; then
  die "ALLOWED_PATHS is not valid JSON"
fi

mkdir -p /tmp
cat > /tmp/sandbox-config.json <<EOF
{
  "plan_id": "${PLAN_ID}",
  "run_id": "${RUN_ID}",
  "sandbox_id": "${SANDBOX_ID}",
  "timeout_minutes": ${TIMEOUT_MINUTES},
  "branch_name": "${BRANCH_NAME}",
  "started_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
log "4.5.1 sandbox-config written"

# ---------------------------------------------------------------------------
# 4.5.2: Lock all files in /workspace to 644 (dirs to 755 implicitly via find)
# ---------------------------------------------------------------------------
if [ -d /workspace ]; then
  # Defensive: only chmod files we actually own/can change. Bind-mount semantics
  # mean some inodes may already be root-owned and that's fine.
  find /workspace -type f -exec chmod 644 {} + 2>/dev/null || true
  find /workspace -type d -exec chmod 755 {} + 2>/dev/null || true
  # .git dirs need exec bit (already 755 from above, but be explicit if present).
  [ -d /workspace/.git ] && chmod -R u+rwX,go+rX /workspace/.git || true
  log "4.5.2 workspace locked to read-only baseline"
else
  log "4.5.2 SKIP — /workspace not mounted"
fi

# ---------------------------------------------------------------------------
# 4.5.3: Open writable paths for aiagent per ALLOWED_PATHS.write
# ---------------------------------------------------------------------------
if [ -d /workspace ]; then
  WRITE_PATHS=$(echo "${ALLOWED_PATHS}" | jq -r '.write[]?')
  while IFS= read -r p; do
    [ -z "$p" ] && continue
    full="/workspace/${p}"
    if [ -d "$full" ]; then
      chmod 755 "$full" || true
      find "$full" -type f -exec chmod 644 {} + 2>/dev/null || true
      chown -R aiagent:agentgroup "$full" 2>/dev/null || true
    elif [ -f "$full" ]; then
      chmod 644 "$full" || true
      chown aiagent:agentgroup "$full" 2>/dev/null || true
    else
      log "4.5.3 WARN — writable path missing: ${full}"
    fi
  done <<< "$WRITE_PATHS"
  log "4.5.3 writable paths opened"
fi

# ---------------------------------------------------------------------------
# 4.5.4 + 4.5.5: Verify git is accessible and configure safe.directory
# ---------------------------------------------------------------------------
if [ -d /workspace/.git ]; then
  # Configure safe.directory for both root and aiagent (system-wide).
  git config --system --add safe.directory /workspace || true
  runuser -s /bin/bash -c "git -C /workspace status --short" aiagent >/dev/null 2>&1 \
    || log "4.5.4 WARN — aiagent git status produced no output (may be clean)"
  log "4.5.4/5 git accessible + safe.directory set"
else
  log "4.5.4 SKIP — /workspace/.git absent"
fi

# ---------------------------------------------------------------------------
# 4.5.6: Install pre-push hook blocking origin pushes.
# Delegates to scripts/docker-git-hooks.sh (baked into the image at
# /opt/lcm-sandbox/git-hooks/docker-git-hooks.sh) so the same logic handles
# both normal repos and worktree-linked .git files.
# ---------------------------------------------------------------------------
if [ -x /opt/lcm-sandbox/git-hooks/docker-git-hooks.sh ]; then
  /opt/lcm-sandbox/git-hooks/docker-git-hooks.sh /workspace || log "4.5.6 WARN — hook install failed"
  log "4.5.6 pre-push hook install completed"
else
  log "4.5.6 SKIP — docker-git-hooks.sh not found in image"
fi

# ---------------------------------------------------------------------------
# 4.5.7: Write .sandbox-manifest.json into the worktree
# ---------------------------------------------------------------------------
if [ -d /workspace ]; then
  TIMEOUT_AT=$(date -u -d "+${TIMEOUT_MINUTES} minutes" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null \
               || python3 -c "import datetime, os; print((datetime.datetime.utcnow()+datetime.timedelta(minutes=int(os.environ['TIMEOUT_MINUTES']))).strftime('%Y-%m-%dT%H:%M:%SZ'))")
  cat > /workspace/.sandbox-manifest.json <<EOF
{
  "plan_id": "${PLAN_ID}",
  "run_id": "${RUN_ID}",
  "sandbox_id": "${SANDBOX_ID}",
  "started_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "timeout_minutes": ${TIMEOUT_MINUTES},
  "timeout_at": "${TIMEOUT_AT}",
  "branch_name": "${BRANCH_NAME}",
  "allowed_paths": ${ALLOWED_PATHS},
  "container_user": "aiagent",
  "container_id": "$(hostname)"
}
EOF
  chown aiagent:agentgroup /workspace/.sandbox-manifest.json 2>/dev/null || true
  log "4.5.7 sandbox manifest written"
fi

# ---------------------------------------------------------------------------
# 4.5.7a: Apply in-sandbox agent profile (permissive | standard).
# See SANDBOX-AGENT-CONFIG.md §"How the entrypoint applies the profile".
# ---------------------------------------------------------------------------
LCM_AGENT_PROFILE="${LCM_AGENT_PROFILE:-permissive}"
case "${LCM_AGENT_PROFILE}" in
  permissive|standard) ;;
  *) die "unknown LCM_AGENT_PROFILE='${LCM_AGENT_PROFILE}' (expected permissive|standard)" ;;
esac
if [ -x /opt/lcm-sandbox/apply_agent_profile.py ]; then
  LCM_PROFILE_TEMPLATE_DIR=/opt/lcm-sandbox/agent_profiles \
    /opt/lcm-sandbox/apply_agent_profile.py \
      --profile "${LCM_AGENT_PROFILE}" \
      --target /home/aiagent \
    || die "apply_agent_profile failed"
  log "4.5.7a agent profile '${LCM_AGENT_PROFILE}' applied"
else
  log "4.5.7a SKIP — apply_agent_profile.py not found in image"
fi

# ---------------------------------------------------------------------------
# 4.5.7b: Bootstrap smoke test. Refuses to drop to the agent if any check fails.
# See SANDBOX-AGENT-CONFIG.md §4 "Bootstrap smoke test".
# ---------------------------------------------------------------------------
if [ -x /opt/lcm-sandbox/smoke-test.sh ]; then
  if ! /opt/lcm-sandbox/smoke-test.sh /workspace; then
    die "bootstrap smoke test failed — refusing to launch agent"
  fi
  log "4.5.7b smoke test passed"
else
  log "4.5.7b SKIP — smoke-test.sh not found in image"
fi

# ---------------------------------------------------------------------------
# 4.5.8: Switch to aiagent. Dockerfile.hermes sets USER aiagent at build time
# but the launcher does NOT pass --user, so this script runs as root. We use
# `su` / `gosu`-style invocation to drop privileges for the remaining steps.
# ---------------------------------------------------------------------------
RUN_AS_AIAGENT() { runuser -s /bin/bash -c "$1" aiagent; }

# ---------------------------------------------------------------------------
# 4.5.9: Verify git state as aiagent (informational; non-fatal)
# ---------------------------------------------------------------------------
if [ -d /workspace/.git ]; then
  RUN_AS_AIAGENT "git -C /workspace log -1 --oneline" >&2 || log "4.5.9 git log failed"
fi

# ---------------------------------------------------------------------------
# 4.5.10 (extended): Hermes branch.
# If HERMES_PERSONA is set, render persona state and start the gateway as a
# background daemon. Else fall back to interactive bash (legacy behavior).
# ---------------------------------------------------------------------------
HERMES_PERSONA="${HERMES_PERSONA:-}"
if [ -n "${HERMES_PERSONA}" ]; then
  log "4.5.10 Hermes mode — persona=${HERMES_PERSONA}"

  # The renderer (WP-8) is invoked from the HOST side via `docker cp` of
  # already-rendered files; if the launcher chose in-container rendering it
  # will set HERMES_PRERENDERED=1 to signal "files already in place".
  HERMES_HOME="${HERMES_HOME:-/home/aiagent/.hermes}"
  mkdir -p "${HERMES_HOME}"
  chown -R aiagent:agentgroup "${HERMES_HOME}" 2>/dev/null || true

  if [ "${HERMES_PRERENDERED:-0}" = "1" ]; then
    log "4.5.10 persona files pre-rendered; skipping in-container render"
  else
    # In-container render path (used by the smoke test / dev mode). Requires the
    # AIDevOps platform API to be reachable from the container.
    PLATFORM_API_BASE="${PLATFORM_API_BASE:-http://host.docker.internal:9700}"
    AIDEVOPS_API_KEY="${AIDEVOPS_API_KEY:-dev-key}"
    PERSONA_REPO_PATH="${PERSONA_REPO_PATH:-/workspace/aidevops-hermes-personas}"
    if RUN_AS_AIAGENT "command -v persona-state-renderer" >/dev/null 2>&1; then
      RUN_AS_AIAGENT "persona-state-renderer render \
        --persona-key '${HERMES_PERSONA}' \
        --output-dir '${HERMES_HOME}' \
        --persona-repo-path '${PERSONA_REPO_PATH}' \
        --platform-api-base '${PLATFORM_API_BASE}' \
        --platform-api-key '${AIDEVOPS_API_KEY}' \
        --mcp-url '${MCP_SERVER_URL:-}' \
        --mcp-token '${MCP_TOKEN:-}' \
        --model-provider '${MODEL_PROVIDER:-}' \
        --model-key '${MODEL_KEY:-}'" \
        || die "persona-state-renderer failed"
    else
      log "4.5.10 WARN — persona-state-renderer not installed in image; skipping render"
    fi
  fi

  # Start Hermes gateway as background daemon.
  log "4.5.10 starting hermes gateway"
  rm -f /tmp/hermes-gateway.log
  RUN_AS_AIAGENT "nohup hermes gateway >/tmp/hermes-gateway.log 2>&1 &"

  # Health-check: poll /health up to 30s.
  deadline=$((SECONDS + 30))
  healthy=0
  while [ $SECONDS -lt $deadline ]; do
    if curl -sS -o /dev/null -w '%{http_code}' "http://localhost:8642/health" 2>/dev/null | grep -q '^200$'; then
      healthy=1
      break
    fi
    sleep 1
  done
  if [ $healthy -eq 1 ]; then
    log "4.5.10 hermes gateway healthy on :8642"
  else
    log "4.5.10 WARN — hermes gateway did not report healthy in 30s; check /tmp/hermes-gateway.log"
    tail -n 50 /tmp/hermes-gateway.log >&2 || true
  fi

  # Hold the container until external teardown.
  exec sleep infinity
fi

# ---------------------------------------------------------------------------
# Non-Hermes path: drop to interactive bash as aiagent (legacy behavior).
# If stdin is not a TTY (e.g. detached launch), hold the container instead.
# ---------------------------------------------------------------------------
if [ -t 0 ]; then
  exec runuser -s /bin/bash -l aiagent
else
  log "4.5.10 no TTY and no HERMES_PERSONA — holding container with sleep infinity"
  exec sleep infinity
fi
