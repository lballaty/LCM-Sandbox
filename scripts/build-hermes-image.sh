#!/usr/bin/env bash
# File: LCM-Sandbox/scripts/build-hermes-image.sh
# Description: Build the lcm-hermes-agent Docker image. Hermes-variant of the sandbox image.
# Author: Libor Ballaty <libor@arionetworks.com>
# Created: 2026-06-09
#
# Usage:
#   ./scripts/build-hermes-image.sh                  # default tag lcm-hermes-agent:latest
#   ./scripts/build-hermes-image.sh v1.0.0           # custom tag
#   FORCE_REBUILD=1 ./scripts/build-hermes-image.sh  # ignore cache

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ROOT_DIR="$( cd "${SCRIPT_DIR}/.." && pwd )"
DOCKERFILE="${SCRIPT_DIR}/Dockerfile.hermes"
TAG_SUFFIX="${1:-latest}"
IMAGE_TAG="lcm-hermes-agent:${TAG_SUFFIX}"

if [ ! -f "${DOCKERFILE}" ]; then
    echo "ERROR: ${DOCKERFILE} not found" >&2
    exit 1
fi

BUILD_FLAGS=""
if [ "${FORCE_REBUILD:-0}" = "1" ]; then
    BUILD_FLAGS="--no-cache"
fi

echo "==> Building ${IMAGE_TAG}"
echo "    Dockerfile: ${DOCKERFILE}"
echo "    Context:    ${ROOT_DIR}"
echo "    Flags:      ${BUILD_FLAGS:-(cache enabled)}"

docker build ${BUILD_FLAGS} \
    -f "${DOCKERFILE}" \
    -t "${IMAGE_TAG}" \
    "${ROOT_DIR}"

echo "==> Built ${IMAGE_TAG}"
docker images "${IMAGE_TAG}"

echo ""
echo "==> Quick verify:"
docker run --rm "${IMAGE_TAG}" hermes --version
