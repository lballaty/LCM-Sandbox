#!/bin/bash
# File: LCM-Sandbox/scripts/rm-shim.sh
# Description: Defensive `rm` wrapper installed at /usr/local/bin/rm inside the
#              lcm-hermes-agent container. Rejects rm targets that resolve
#              outside of the workspace-and-tmp safelist (/workspace, /tmp,
#              /home/aiagent/.cache, /var/tmp). Inside the safelist `rm` is
#              unrestricted. Required by SANDBOX-AGENT-CONFIG.md §4 as one of
#              the layered defenses for the permissive agent profile.
# Author: Libor Ballaty <libor@arionetworks.com>
# Created: 2026-06-16
#
# Implementation notes:
#   - We forward to /bin/rm for ALL flag handling so this shim does not need to
#     understand rm's option grammar. We only inspect non-flag arguments to
#     decide whether they are inside the safelist.
#   - Symlinks: we use `readlink -f` to canonicalize so an agent cannot
#     escape via `ln -s / /workspace/escape; rm -rf /workspace/escape/etc`.
#   - `--` (end-of-options) is respected: anything after `--` is treated as
#     a path regardless of leading dashes.

set -euo pipefail

SAFE_PREFIXES=(
  "/workspace"
  "/tmp"
  "/var/tmp"
  "/home/aiagent/.cache"
  "/home/aiagent/.local"
)

is_safe() {
  local target="$1"
  local resolved
  # readlink -f resolves symlinks AND non-existent tail components. If the path
  # is fully gone (e.g. rm after an earlier rm), fall back to logical
  # canonicalisation via realpath -m.
  if resolved=$(readlink -f -- "${target}" 2>/dev/null) && [[ -n "${resolved}" ]]; then
    :
  elif resolved=$(realpath -m -- "${target}" 2>/dev/null); then
    :
  else
    resolved="${target}"
  fi
  for prefix in "${SAFE_PREFIXES[@]}"; do
    if [[ "${resolved}" == "${prefix}" || "${resolved}" == "${prefix}"/* ]]; then
      return 0
    fi
  done
  return 1
}

# Walk argv: collect flag args verbatim; for non-flag args (or anything after
# `--`), validate against the safelist before letting it through.
end_of_opts=0
denied=()
for arg in "$@"; do
  if [[ "${end_of_opts}" -eq 1 ]] || [[ "${arg}" != -* ]]; then
    if ! is_safe "${arg}"; then
      denied+=("${arg}")
    fi
  fi
  if [[ "${arg}" == "--" ]]; then
    end_of_opts=1
  fi
done

if [[ "${#denied[@]}" -gt 0 ]]; then
  echo "ERROR: rm-shim blocked one or more paths outside the sandbox safelist:" >&2
  for p in "${denied[@]}"; do echo "  - ${p}" >&2; done
  echo "Safelist:" >&2
  for p in "${SAFE_PREFIXES[@]}"; do echo "  - ${p}/*" >&2; done
  exit 1
fi

exec /bin/rm "$@"
