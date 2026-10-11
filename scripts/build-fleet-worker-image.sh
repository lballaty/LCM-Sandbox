#!/usr/bin/env bash
# File: LCM-Sandbox/scripts/build-fleet-worker-image.sh
# Description: Build the lcm-fleet-worker Docker image (P1). Overlay on the
#              hardened lcm-hermes-agent image adding claude/codex/gemini/agy
#              and tmux, so one disposable unit can run any agent in the fleet.
# Author: Claude (Opus 5) — ai-dev-dotfiles-d0
# Created: 2026-10-11
#
# Usage:
#   ./scripts/build-fleet-worker-image.sh                   # tag lcm-fleet-worker:latest
#   ./scripts/build-fleet-worker-image.sh v1.0.0            # custom tag
#   FORCE_REBUILD=1 ./scripts/build-fleet-worker-image.sh   # ignore cache
#   AGY_INSTALLER_SHA256=<hex> ./scripts/build-fleet-worker-image.sh
#   BASE_IMAGE=lcm-hermes-agent:v1.0.0 ./scripts/build-fleet-worker-image.sh
#
# The agy installer is fetched over the network and executed. Unpinned, that is
# a supply-chain hole. The first build PRINTS the installer's sha256; pin it via
# AGY_INSTALLER_SHA256 for every build after that, and always for a shared or
# hardened build.

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ROOT_DIR="$( cd "${SCRIPT_DIR}/.." && pwd )"
DOCKERFILE="${SCRIPT_DIR}/Dockerfile.fleet-worker"
TAG_SUFFIX="${1:-latest}"
IMAGE_TAG="lcm-fleet-worker:${TAG_SUFFIX}"
BASE_IMAGE="${BASE_IMAGE:-lcm-hermes-agent:latest}"

if [ ! -f "${DOCKERFILE}" ]; then
    echo "ERROR: ${DOCKERFILE} not found" >&2
    exit 1
fi

# This is an OVERLAY: the base must already exist locally. Checking here turns
# an opaque "pull access denied for lcm-hermes-agent" into an actionable line.
if ! docker image inspect "${BASE_IMAGE}" >/dev/null 2>&1; then
    echo "ERROR: base image '${BASE_IMAGE}' not found locally." >&2
    echo "       The fleet worker is an overlay on the hardened Hermes image." >&2
    echo "       Build it first:" >&2
    echo "         ${SCRIPT_DIR}/build-hermes-image.sh" >&2
    exit 1
fi

BUILD_FLAGS=""
if [ "${FORCE_REBUILD:-0}" = "1" ]; then
    BUILD_FLAGS="--no-cache"
fi

if [ -n "${AGY_INSTALLER_SHA256:-}" ]; then
    echo "==> agy installer PINNED to ${AGY_INSTALLER_SHA256}"
else
    echo "==> WARNING: agy installer NOT pinned (AGY_INSTALLER_SHA256 unset)."
    echo "    The build will print the hash it fetched — pin it for future builds."
fi

echo "==> Building ${IMAGE_TAG}"
echo "    Dockerfile: ${DOCKERFILE}"
echo "    Base:       ${BASE_IMAGE}"
echo "    Context:    ${ROOT_DIR}"
echo "    Flags:      ${BUILD_FLAGS:-(cache enabled)}"

docker build ${BUILD_FLAGS} \
    -f "${DOCKERFILE}" \
    --build-arg "BASE_IMAGE=${BASE_IMAGE}" \
    --build-arg "AGY_INSTALLER_SHA256=${AGY_INSTALLER_SHA256:-}" \
    -t "${IMAGE_TAG}" \
    "${ROOT_DIR}"

echo "==> Built ${IMAGE_TAG}"
docker images "${IMAGE_TAG}"

# Acceptance check for P1: all five agent CLIs usable, tmux present, and the
# confinement the image inherits still intact. The Dockerfile asserts these at
# build time too; repeating them here catches a tag that was built from an
# older Dockerfile, which a build-time assertion cannot.
echo ""
echo "==> Verifying the worker image"
docker run --rm --entrypoint /bin/bash "${IMAGE_TAG}" -lc '
    set -u
    fail=0
    for c in claude codex gemini hermes agy tmux; do
        if command -v "$c" >/dev/null 2>&1; then
            printf "    OK   %-8s %s\n" "$c" "$(command -v $c)"
        else
            printf "    FAIL %-8s not found\n" "$c"; fail=1
        fi
    done
    if command -v sudo >/dev/null 2>&1; then
        echo "    FAIL sudo is present in a worker image"; fail=1
    else
        echo "    OK   no sudo"
    fi
    if [ "$(command -v rm)" = "/usr/local/bin/rm" ]; then
        echo "    OK   rm-shim first on PATH"
    else
        echo "    FAIL rm-shim not active (got: $(command -v rm))"; fail=1
    fi
    for v in FLEET_ID QUEUECTL_AGENT AGENT_NAME NODE_ID; do
        if [ -n "${!v:-}" ]; then
            echo "    FAIL $v is baked into the image (must be injected at run)"; fail=1
        fi
    done
    [ "$fail" -eq 0 ] && echo "    -- worker image verified --"
    exit "$fail"
'

echo ""
echo "==> ${IMAGE_TAG} ready."
echo "    Identity and credentials are injected at launch, never baked:"
echo "      docker run --rm -it \\"
echo "        -e FLEET_ID=<fleet> -e QUEUECTL_AGENT=<fleet>-<agent> \\"
echo "        -e AGENT_NAME=<agent> -e NODE_ID=<host> \\"
echo "        -e ANTHROPIC_API_KEY=... \\"
echo "        -v <repo-or-worktree>:/workspace \\"
echo "        -v ~/.lcm-sandbox/runs/<id>:/control \\"
echo "        ${IMAGE_TAG}"
