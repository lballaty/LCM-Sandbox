#!/usr/bin/env bash
# File: LCM-Sandbox/scripts/stop-dev-sandbox.sh
# Description: Stop and remove a dev-sandbox container. Refuses to act on
#              containers that were not launched from the lcm-dev-sandbox
#              image, so an agent that learns this script's path cannot
#              accidentally or deliberately destroy unrelated containers.
#              Pinned to the colima-lcm-sandbox docker context.
# Author: Libor Ballaty <libor@arionetworks.com>
# Created: 2026-06-16
# Usage:
#   scripts/stop-dev-sandbox.sh <container-name>

set -euo pipefail

PROFILE="lcm-sandbox"
CONTEXT="colima-${PROFILE}"
EXPECTED_IMAGE="lcm-dev-sandbox:latest"
NAME="${1:-}"
[[ -n "$NAME" ]] || { echo "usage: $0 <container-name>" >&2; exit 2; }

# Container must exist on the correct context.
if ! docker --context "$CONTEXT" inspect "$NAME" >/dev/null 2>&1; then
  echo "container '$NAME' not found on $CONTEXT" >&2
  exit 3
fi

# Container must be built from the dev-sandbox image. Refuses to remove anything else.
image="$(docker --context "$CONTEXT" inspect --format '{{.Config.Image}}' "$NAME")"
if [[ "$image" != "$EXPECTED_IMAGE" ]]; then
  echo "refusing to remove '$NAME': image is '$image', expected '$EXPECTED_IMAGE'" >&2
  exit 4
fi

# Second-line defense: ownership label set by run-dev-sandbox.sh.
# Containers without the label may have been launched by an older version of
# the launcher; allow them through with a warning rather than blocking.
label="$(docker --context "$CONTEXT" inspect --format '{{index .Config.Labels "lcm-dev-sandbox"}}' "$NAME" 2>/dev/null || true)"
if [[ "$label" != "managed" ]]; then
  echo "warning: '$NAME' has no lcm-dev-sandbox=managed label (pre-2026-06-16 container?); proceeding" >&2
fi

state="$(docker --context "$CONTEXT" inspect --format '{{.State.Status}}' "$NAME")"
if [[ "$state" == "running" ]]; then
  docker --context "$CONTEXT" stop "$NAME" >/dev/null
fi
docker --context "$CONTEXT" rm "$NAME" >/dev/null
echo "removed: $NAME"
