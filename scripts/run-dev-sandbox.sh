#!/usr/bin/env bash
# File: LCM-Sandbox/scripts/run-dev-sandbox.sh
# Description: Launch a manual dev sandbox container from the lcm-dev-sandbox image.
#              Bind-mounts a host repo at /workspace and the agent dotfiles at
#              /home/aiagent. Designed as a reusable template — change REPO_PATH and
#              CONTAINER_NAME for each new sandbox.
#
#              Mount policy:
#                - .claude, .codex, .gemini  rw   (CLIs must persist OAuth tokens / aliases)
#                - .ai-dev-dotfiles          ro   (shared agent config, container shouldn't mutate)
#                - .gitconfig                ro   (git identity only)
#                - repo at /workspace        rw   (the whole point)
#
# Author: Libor Ballaty <libor@arionetworks.com>
# Created: 2026-06-15
# Usage:
#   scripts/run-dev-sandbox.sh                          # uses defaults below
#   REPO_PATH=/path/to/repo CONTAINER_NAME=name scripts/run-dev-sandbox.sh
#
# Extra mounts (newline-separated `host:container[:mode]` triples):
#   EXTRA_MOUNTS=$'/tmp/data:/data:ro\n/var/log:/host-log:ro' scripts/run-dev-sandbox.sh

set -euo pipefail

# --- Configurable defaults -------------------------------------------------
: "${IMAGE_TAG:=lcm-dev-sandbox:latest}"
: "${CONTAINER_NAME:=lcm-sandbox-fasthashapi}"
: "${REPO_PATH:=/Users/liborballaty/LocalProjects/GitHubProjectsDocuments/fasthashAPI}"
: "${HOST_HOME:=/Users/liborballaty}"
: "${EXTRA_MOUNTS:=}"
# ---------------------------------------------------------------------------

if [[ ! -d "$REPO_PATH" ]]; then
  echo "error: REPO_PATH does not exist: $REPO_PATH" >&2
  exit 1
fi

if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
  echo "container '$CONTAINER_NAME' already exists. Stop and remove it first:"
  echo "  docker stop $CONTAINER_NAME && docker rm $CONTAINER_NAME"
  exit 1
fi

extra_mount_args=()
if [[ -n "$EXTRA_MOUNTS" ]]; then
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    extra_mount_args+=("-v" "$line")
  done <<< "$EXTRA_MOUNTS"
fi

docker run -d --name "$CONTAINER_NAME" \
  -v "$REPO_PATH:/workspace" \
  -v "$HOST_HOME/.claude:/home/aiagent/.claude" \
  -v "$HOST_HOME/.codex:/home/aiagent/.codex" \
  -v "$HOST_HOME/.gemini:/home/aiagent/.gemini" \
  -v "$HOST_HOME/.ai-dev-dotfiles:/home/aiagent/.ai-dev-dotfiles:ro" \
  -v "$HOST_HOME/.gitconfig:/home/aiagent/.gitconfig:ro" \
  "${extra_mount_args[@]}" \
  -w /workspace \
  "$IMAGE_TAG" \
  sleep infinity

echo "started: $CONTAINER_NAME"
echo "enter:   docker exec -it $CONTAINER_NAME bash"
