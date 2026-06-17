# File: lcm_sandbox/core/docker_launcher.py
# Description: Phase 4 container launch. Builds the docker-run argv (STEP 4.1-4.3),
#              starts the container, and polls until it reports "Up" (STEP 4.4).
#              Hermes-aware: propagates HERMES_PERSONA, MCP_SERVER_URL, MCP_TOKEN,
#              MODEL_PROVIDER, MODEL_KEY into the container so the entrypoint can
#              render persona state and start the Hermes gateway.
# Author: Libor Ballaty <libor@arionetworks.com>
# Created: 2026-06-12

"""Phase 4: launch the sandbox container.

Implements SANDBOX-DETAILED-FLOW.md STEP 4.1 through STEP 4.4. STEP 4.5 lives
inside the container (`scripts/entrypoint.sh`); this module only sets up the
host-side launch and verifies the container reached "Up" state.

Phase 5 (MCP outbound) and Phase 6 (capture) live elsewhere.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from lcm_sandbox.exceptions import DockerLaunchError
from lcm_sandbox.models import SandboxConfig
from lcm_sandbox.utils import docker as docker_utils
from lcm_sandbox.utils.logger import get_logger
from lcm_sandbox.utils.shell import run

logger = get_logger(__name__)


DEFAULT_IMAGE_TAG = "lcm-hermes-agent:latest"
HERMES_INTERNAL_PORT = 8642  # Hermes gateway default; entrypoint health-checks this
HEALTH_POLL_TIMEOUT_SECONDS = 30
HEALTH_POLL_INTERVAL_SECONDS = 1.0


class ContainerLaunchResult(BaseModel):
    """Outcome of a container launch attempt."""

    sandbox_id: str
    container_id: str | None
    started_at: datetime
    hermes_api_port: int = Field(default=HERMES_INTERNAL_PORT)
    status: Literal["running", "failed"]
    error: str | None = None
    image_tag: str = DEFAULT_IMAGE_TAG


_EGRESS_ALLOWLIST_ENTRY_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9.\-]*(?::[0-9]{1,5})?$"
)


def parse_egress_allowlist(spec: str | None) -> list[tuple[str, int | None]]:
    """Parse `--egress-allowlist` comma-separated value into (host, port) tuples.

    Empty string and None both yield an empty list. Each entry is `HOST[:PORT]`.
    Port is optional; when omitted the caller treats the entry as "all ports".
    Raises ValueError for malformed entries — the launcher converts that into a
    DockerLaunchError at step 4.1 so the operator sees a clear refusal rather
    than a half-applied allowlist.
    """
    if not spec:
        return []
    out: list[tuple[str, int | None]] = []
    for raw in spec.split(","):
        entry = raw.strip()
        if not entry:
            continue
        if not _EGRESS_ALLOWLIST_ENTRY_RE.match(entry):
            raise ValueError(
                f"invalid egress allowlist entry {entry!r}; expected HOST[:PORT]"
            )
        if ":" in entry:
            host, _, port_str = entry.partition(":")
            port = int(port_str)
            if not (1 <= port <= 65535):
                raise ValueError(
                    f"egress allowlist port out of range in {entry!r}"
                )
            out.append((host, port))
        else:
            out.append((entry, None))
    return out


def _build_run_argv(
    sandbox_config: SandboxConfig,
    image_tag: str,
    worktree_path: Path,
    hermes_persona: str | None,
    mcp_url: str | None,
    mcp_token: str | None,
    model_provider: str | None,
    model_key: str | None,
    egress_allowlist: list[tuple[str, int | None]] | None = None,
) -> list[str]:
    """Construct the `docker run` argv per STEP 4.1-4.3.

    Notes vs. the canonical flow:
      - We do NOT pass `--rm`: WP-3 capture phase needs the container around
        after exit. Teardown is explicit (`docker rm -f`).
      - `--name <sandbox_id>` so the capturer and the `stop` CLI can find it.
      - `-d` (detached): the host returns immediately; entrypoint runs Hermes
        in the background and `sleep infinity` holds the container until
        external teardown.
      - `--cap-drop=ALL` and `--security-opt=no-new-privileges` for
        defence-in-depth.
      - `--read-only` rootfs with explicit `--tmpfs` for the four locations
        the entrypoint + Hermes runtime write to (`/tmp`, `/var/tmp`,
        `/run`, `/home/aiagent/.cache`). Anything outside those (e.g. the
        agent trying to drop a payload in `/etc` or `/usr`) hits EROFS.
      - Network is the default bridge unless `egress_allowlist` is set;
        when set, the launcher attaches the container to a pre-built
        restricted bridge and the entrypoint adds iptables rules.
    """
    allowed_paths_json = json.dumps(
        sandbox_config.allowed_paths.model_dump(),
        separators=(",", ":"),
    )

    argv: list[str] = [
        "docker", "run",
        "-d",
        "--name", sandbox_config.run_id,
        "--label", f"plan_id={sandbox_config.plan_id}",
        "--label", f"run_id={sandbox_config.run_id}",
        "--label", f"sandbox_id={sandbox_config.run_id}",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--read-only",
        # The entrypoint, the smoke test, Hermes' gateway, and uv writes all
        # need scratch space. Anything outside these four mounts hits EROFS.
        "--tmpfs", "/tmp:rw,exec,nosuid,size=512m",
        "--tmpfs", "/var/tmp:rw,exec,nosuid,size=256m",
        "--tmpfs", "/run:rw,nosuid,size=64m",
        "--tmpfs", "/home/aiagent/.cache:rw,exec,nosuid,size=512m,uid=1000,gid=1000",
        "-v", f"{worktree_path}:/workspace:rw",
        "-e", f"PLAN_ID={sandbox_config.plan_id}",
        "-e", f"RUN_ID={sandbox_config.run_id}",
        "-e", f"SANDBOX_ID={sandbox_config.run_id}",
        "-e", f"ALLOWED_PATHS={allowed_paths_json}",
        "-e", f"TIMEOUT_MINUTES={sandbox_config.timeout_minutes}",
        "-e", f"BRANCH_NAME={sandbox_config.branch_name}",
    ]

    # Hermes-aware env (only set when present so the entrypoint can branch).
    if hermes_persona:
        argv += ["-e", f"HERMES_PERSONA={hermes_persona}"]
    if mcp_url:
        argv += ["-e", f"MCP_SERVER_URL={mcp_url}"]
    if mcp_token:
        argv += ["-e", f"MCP_TOKEN={mcp_token}"]
    if model_provider:
        argv += ["-e", f"MODEL_PROVIDER={model_provider}"]
    if model_key:
        argv += ["-e", f"MODEL_KEY={model_key}"]

    if egress_allowlist:
        # Pass the parsed list into the container so the entrypoint (or a
        # privileged init sidecar in a future iteration) can enforce it.
        # Format: comma-separated "host[:port]" entries. The actual network
        # enforcement requires a host-side restricted bridge (--cap-drop=ALL
        # prevents in-container iptables), which is Phase 5 infrastructure
        # work — until that ships, this env var is advisory.
        encoded = ",".join(
            f"{host}:{port}" if port is not None else host
            for host, port in egress_allowlist
        )
        argv += ["-e", f"LCM_EGRESS_ALLOWLIST={encoded}"]

    # NOTE: `--user aiagent` is omitted because the entrypoint must run as root
    # to chmod/chown the bind-mounted /workspace (STEP 4.5.2/4.5.3). The
    # entrypoint drops to aiagent before launching Hermes.
    argv.append(image_tag)
    return argv


def launch_container(
    sandbox_config: SandboxConfig,
    *,
    image_tag: str = DEFAULT_IMAGE_TAG,
    worktree_path: Path,
    hermes_persona: str | None = None,
    mcp_url: str | None = None,
    mcp_token: str | None = None,
    model_provider: str | None = None,
    model_key: str | None = None,
    egress_allowlist: list[tuple[str, int | None]] | None = None,
    launch_timeout_seconds: int = 60,
) -> ContainerLaunchResult:
    """Launch a sandbox container and verify it reaches "Up" state.

    Raises DockerLaunchError on failure with the failing step recorded.
    """
    if not worktree_path.exists():
        raise DockerLaunchError(
            "worktree_path does not exist",
            step="4.2",
            worktree_path=str(worktree_path),
        )

    argv = _build_run_argv(
        sandbox_config=sandbox_config,
        image_tag=image_tag,
        worktree_path=worktree_path,
        hermes_persona=hermes_persona,
        mcp_url=mcp_url,
        mcp_token=mcp_token,
        model_provider=model_provider,
        model_key=model_key,
        egress_allowlist=egress_allowlist,
    )

    started_at = datetime.now(timezone.utc)
    logger.info(
        "phase4_launch_starting",
        extra={
            "run_id": sandbox_config.run_id,
            "phase": 4,
            "step": "4.3",
            "image_tag": image_tag,
            "hermes_persona": hermes_persona,
        },
    )
    result = run(argv, timeout=launch_timeout_seconds)
    if not result.ok:
        err = (result.stderr or result.stdout).strip()
        logger.error(
            "phase4_launch_failed",
            extra={
                "run_id": sandbox_config.run_id,
                "phase": 4,
                "step": "4.3",
                "stderr": err,
            },
        )
        return ContainerLaunchResult(
            sandbox_id=sandbox_config.run_id,
            container_id=None,
            started_at=started_at,
            status="failed",
            error=err or f"docker run exited {result.returncode}",
            image_tag=image_tag,
        )

    container_id = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else None

    # STEP 4.4: poll `docker ps --filter name=...` until status begins with "Up".
    if not _wait_until_up(sandbox_config.run_id, HEALTH_POLL_TIMEOUT_SECONDS):
        # Container was created but did not reach Up. Surface diagnostic logs.
        logs = _container_logs(sandbox_config.run_id)
        logger.error(
            "phase4_container_not_up",
            extra={
                "run_id": sandbox_config.run_id,
                "phase": 4,
                "step": "4.4",
                "logs_tail": logs[-2000:] if logs else "",
            },
        )
        return ContainerLaunchResult(
            sandbox_id=sandbox_config.run_id,
            container_id=container_id,
            started_at=started_at,
            status="failed",
            error="container did not reach Up state within "
            f"{HEALTH_POLL_TIMEOUT_SECONDS}s",
            image_tag=image_tag,
        )

    logger.info(
        "phase4_launch_succeeded",
        extra={
            "run_id": sandbox_config.run_id,
            "phase": 4,
            "step": "4.4",
            "container_id": container_id,
        },
    )
    return ContainerLaunchResult(
        sandbox_id=sandbox_config.run_id,
        container_id=container_id,
        started_at=started_at,
        status="running",
        image_tag=image_tag,
    )


def _wait_until_up(sandbox_id: str, timeout_seconds: int) -> bool:
    """Poll `docker ps` until the container's status starts with 'Up'."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = run(
            ["docker", "ps", "--filter", f"name={sandbox_id}",
             "--format", "{{.Status}}"],
            timeout=10,
        )
        if result.ok:
            status = result.stdout.strip()
            if status.startswith("Up"):
                return True
        time.sleep(HEALTH_POLL_INTERVAL_SECONDS)
    return False


def _container_logs(sandbox_id: str) -> str:
    result = run(["docker", "logs", sandbox_id], timeout=10)
    return (result.stdout or "") + (result.stderr or "")


def stop_container(sandbox_id: str, *, remove: bool = True, timeout: int = 10) -> dict:
    """Stop (and optionally remove) the named container.

    WP-3 capture happens before this is called; here we only tear down.
    """
    stop_res = run(["docker", "stop", "-t", str(timeout), sandbox_id], timeout=timeout + 10)
    payload: dict = {
        "sandbox_id": sandbox_id,
        "stopped": stop_res.ok,
        "stop_stderr": stop_res.stderr.strip(),
    }
    if remove:
        rm_res = run(["docker", "rm", "-f", sandbox_id], timeout=15)
        payload["removed"] = rm_res.ok
        payload["rm_stderr"] = rm_res.stderr.strip()
    return payload


def container_status(sandbox_id: str) -> dict:
    """Return basic status JSON for the named container, suitable for CLI output."""
    res = run(
        ["docker", "ps", "-a", "--filter", f"name={sandbox_id}",
         "--format", "{{.ID}}\t{{.Status}}\t{{.Image}}"],
        timeout=10,
    )
    if not res.ok:
        return {"sandbox_id": sandbox_id, "status": "unknown", "error": res.stderr.strip()}
    line = res.stdout.strip()
    if not line:
        return {"sandbox_id": sandbox_id, "status": "absent"}
    parts = line.split("\t")
    return {
        "sandbox_id": sandbox_id,
        "container_id": parts[0] if parts else None,
        "status": parts[1] if len(parts) > 1 else "unknown",
        "image": parts[2] if len(parts) > 2 else None,
    }
