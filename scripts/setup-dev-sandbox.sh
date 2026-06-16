#!/usr/bin/env bash
# File: LCM-Sandbox/scripts/setup-dev-sandbox.sh
# Description: Idempotent setup for the manual dev-sandbox. Ensures the
#              colima-lcm-sandbox profile is running, the lcm-dev-sandbox:latest
#              image is built on that profile, and the VM can reach the
#              package repos the build needs. Pinned to the correct docker
#              context on every command so it cannot stray onto another VM.
# Author: Libor Ballaty <libor@arionetworks.com>
# Created: 2026-06-16
# Usage:
#   scripts/setup-dev-sandbox.sh                # no-op if image already exists
#   LCM_FORCE_REBUILD=1 scripts/setup-dev-sandbox.sh   # rebuild from scratch
# Takes no command-line arguments. Configuration is via env vars only so the
# agent permission rule can be an exact-match path.

set -euo pipefail

PROFILE="lcm-sandbox"
CONTEXT="colima-${PROFILE}"
IMAGE="lcm-dev-sandbox:latest"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_CTX="$(cd "${SCRIPT_DIR}/.." && pwd)"
DOCKERFILE="${SCRIPT_DIR}/Dockerfile.dev-sandbox"
FORCE_REBUILD="${LCM_FORCE_REBUILD:-0}"

log() { printf '[setup-dev-sandbox] %s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

# --- 1. Required tools ---
command -v colima >/dev/null 2>&1 || die "colima not on PATH"
command -v docker >/dev/null 2>&1 || die "docker not on PATH"
command -v jq     >/dev/null 2>&1 || die "jq not on PATH"

# --- 2. Profile must exist and be Running ---
log "checking colima profile '${PROFILE}'..."
status="$(colima list -j 2>/dev/null | jq -r --arg p "$PROFILE" 'select(.name==$p) | .status' || true)"
if [[ -z "$status" || "$status" == "null" ]]; then
  die "colima profile '${PROFILE}' does not exist. Create it with: colima start ${PROFILE} --cpu 4 --memory 8 --disk 60 --arch aarch64 --runtime docker"
fi
if [[ "$status" != "Running" ]]; then
  log "profile '${PROFILE}' is ${status}; starting..."
  colima start "${PROFILE}" >&2
fi
log "profile '${PROFILE}' is Running"

# --- 3. Context must exist ---
if ! docker context inspect "${CONTEXT}" >/dev/null 2>&1; then
  die "docker context '${CONTEXT}' not found. Re-create the colima profile."
fi

# --- 4. Idempotent: skip build if image already exists on the correct context ---
if [[ "$FORCE_REBUILD" != "1" ]] && docker --context "${CONTEXT}" image inspect "${IMAGE}" >/dev/null 2>&1; then
  log "image '${IMAGE}' already present on ${CONTEXT} - skipping build (set LCM_FORCE_REBUILD=1 to override)"
  docker --context "${CONTEXT}" image inspect "${IMAGE}" --format 'ready: id={{.Id}} size={{.Size}}'
  exit 0
fi

# --- 5. Pre-flight: VM must reach NodeSource (Node 20 install step) ---
log "checking VM DNS for deb.nodesource.com..."
if ! colima ssh -p "${PROFILE}" -- getent hosts deb.nodesource.com >/dev/null 2>&1; then
  die "VM '${PROFILE}' cannot resolve deb.nodesource.com. Likely causes: (1) Privoxy or other proxy intercepting DNS, (2) VM DNS misconfigured. Fix before retry."
fi
log "VM DNS OK"

# --- 6. Build with full plain output so failures are diagnosable ---
log "building ${IMAGE} on ${CONTEXT}..."
start_ts="$(date +%s)"
docker --context "${CONTEXT}" build \
  --progress=plain \
  -f "${DOCKERFILE}" \
  -t "${IMAGE}" \
  "${BUILD_CTX}" >&2
elapsed=$(( $(date +%s) - start_ts ))
log "build completed in ${elapsed}s"

docker --context "${CONTEXT}" image inspect "${IMAGE}" --format 'ready: id={{.Id}} size={{.Size}}'
