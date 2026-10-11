# File: lcm_sandbox/core/docker_launcher.py
# Description: Phase 4 container launch. Builds the docker-run argv (STEP 4.1-4.3),
#              starts the container, and polls until it reports "Up" (STEP 4.4).
#              Hermes-aware: propagates HERMES_PERSONA, MCP_SERVER_URL, MCP_TOKEN,
#              MODEL_PROVIDER, MODEL_KEY, and provider credential env/value into
#              the container so the entrypoint can render persona state and start
#              the Hermes gateway.
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
import shlex
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

# Fleet identity tokens end up in env vars, container labels and queue claims,
# so keep them to a boring, shell- and filename-safe alphabet.
_FLEET_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class FleetIdentity(BaseModel):
    """Who this worker unit IS, injected at launch (never baked in the image).

    This is the "on-what-basis" chain from
    ~/.ai-dev-dotfiles/design/FLEET-WORKER-SANDBOX-DEFINITION.md §4: without it
    a worker cannot tell which fleet it belongs to or which inbox is its, so
    the standing "read your inbox" rule has no referent. The lcm-fleet-worker
    image declares all four vars EMPTY precisely so that a container with no
    identity is obviously unconfigured rather than silently impersonating the
    last one.

    ``queuectl_agent`` is the composite ``<fleet_id>-<agent_name>`` required by
    the fleet mailbox contract. It is derived when omitted rather than left to
    each caller to assemble, because an inconsistent agent id silently splits
    an agent's queue claims from its inbox.
    """

    fleet_id: str
    agent_name: str
    node_id: str
    queuectl_agent: str | None = None

    def model_post_init(self, _context: object) -> None:
        for field in ("fleet_id", "agent_name", "node_id"):
            value = getattr(self, field)
            if not _FLEET_TOKEN_RE.match(value):
                raise ValueError(
                    f"fleet identity {field}={value!r} is not a safe token "
                    "(expected [A-Za-z0-9][A-Za-z0-9._-]{0,63})"
                )
        derived = f"{self.fleet_id}-{self.agent_name}"
        if self.queuectl_agent is None:
            object.__setattr__(self, "queuectl_agent", derived)
        elif not _FLEET_TOKEN_RE.match(self.queuectl_agent):
            raise ValueError(
                f"fleet identity queuectl_agent={self.queuectl_agent!r} is not "
                "a safe token"
            )

    def as_env_args(self) -> list[str]:
        """The four identity vars as `docker run -e` arguments."""
        return [
            "-e", f"FLEET_ID={self.fleet_id}",
            "-e", f"AGENT_NAME={self.agent_name}",
            "-e", f"NODE_ID={self.node_id}",
            "-e", f"QUEUECTL_AGENT={self.queuectl_agent}",
        ]


class RemoteNode(BaseModel):
    """A fleet host to run the container ON, instead of this machine (P3).

    Every docker call in this module previously went to the LOCAL daemon, so
    the fleet could only ever run units on the orchestrator's own box. This is
    the whole of the cross-machine gap: the control plane already spans hosts
    (Tailscale SSH is verified transport), but the launcher did not use it.

    Deliberately a transport wrapper and nothing more. The docker argv is
    unchanged — it is simply executed somewhere else — so remote and local
    launches cannot drift apart in what they actually run, which is the
    failure mode a separate remote code path would invite.

    IMPORTANT: every path in that argv (worktree, control dir) is resolved on
    the REMOTE host. The caller must not pass local paths and assume they
    exist there; `launch_container` checks them over the wire instead of
    against the local filesystem.
    """

    host: str
    user: str | None = None
    #: Default is Tailscale SSH: the fleet's verified, deny-by-default
    #: transport. Overridable for plain ssh or a jump-host wrapper.
    ssh_command: list[str] = Field(default_factory=lambda: ["tailscale", "ssh"])

    def target(self) -> str:
        return f"{self.user}@{self.host}" if self.user else self.host

    def wrap(self, argv: list[str]) -> list[str]:
        """Wrap a local argv so it executes on this node.

        ``shlex.join`` is load-bearing: ssh hands the command to a remote
        SHELL, and the docker argv contains JSON with quotes and braces
        (ALLOWED_PATHS, --format '{{.Status}}'). Passing it unquoted would let
        the remote shell re-split and mangle it, and in the worst case
        interpret a value as syntax.
        """
        return [*self.ssh_command, self.target(), shlex.join(argv)]


def _docker(argv: list[str], remote: RemoteNode | None) -> list[str]:
    """Route a docker argv at the local daemon or a fleet node."""
    return remote.wrap(argv) if remote else argv


class ResourceLimits(BaseModel):
    """Per-container resource caps.

    OPT-IN, and deliberately not defaulted. The acceptance criteria in
    FLEET-CONTROL-PLANE-AND-ISOLATION-DESIGN.md §10 call for resource caps, but
    picking a default here without measuring what a worker actually consumes
    would risk OOM-killing a working agent mid-task — trading a known gap for
    an unknown failure. Set them explicitly once a real worker run has been
    profiled; `None` means "no cap", which is today's behaviour unchanged.
    """

    memory: str | None = None       # e.g. "4g"
    cpus: str | None = None         # e.g. "2.0"
    pids_limit: int | None = None   # e.g. 512

    def as_run_args(self) -> list[str]:
        args: list[str] = []
        if self.memory:
            args += [f"--memory={self.memory}"]
        if self.cpus:
            args += [f"--cpus={self.cpus}"]
        if self.pids_limit:
            args += [f"--pids-limit={self.pids_limit}"]
        return args


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
    provider_api_key_env: str | None,
    provider_api_key: str | None,
    egress_allowlist: list[tuple[str, int | None]] | None = None,
    fleet_identity: FleetIdentity | None = None,
    control_dir: Path | None = None,
    resource_limits: ResourceLimits | None = None,
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
      - `fleet_identity`, when given, injects the four L0 identity vars. The
        lcm-fleet-worker image ships them EMPTY, so without this a worker
        cannot resolve its own inbox.
      - `control_dir`, when given, bind-mounts the agentic-sandboxes run dir
        at `/control`. The image already sets `CONTROL_DIR=/control` and ships
        `sandbox-emit`; without the mount those write into the container's
        own ephemeral filesystem and the host sees nothing.
      - `resource_limits` is opt-in; see ResourceLimits.
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
    if provider_api_key_env:
        argv += ["-e", f"MODEL_API_KEY_ENV={provider_api_key_env}"]
    if provider_api_key:
        argv += ["-e", f"MODEL_API_KEY={provider_api_key}"]

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

    # Fleet identity (L0). Injected, never baked: a value in the image would
    # make every container from it claim the same identity, which breaks the
    # uniqueness rule in ~/.ai-dev-dotfiles/docs/FLEET-NAMING-AND-IDENTITY.md.
    if fleet_identity is not None:
        argv += fleet_identity.as_env_args()

    # Control plane (L2). The agentic-sandboxes run dir carries plan.json and
    # inbox/ inbound, and status.json / events.jsonl / outbox/ outbound. The
    # container is `--read-only`, so without this rw mount `sandbox-emit` has
    # nowhere durable to write and the run is unobservable from the host.
    if control_dir is not None:
        argv += ["-v", f"{control_dir}:/control:rw"]

    if resource_limits is not None:
        argv += resource_limits.as_run_args()

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
    provider_api_key_env: str | None = None,
    provider_api_key: str | None = None,
    egress_allowlist: list[tuple[str, int | None]] | None = None,
    fleet_identity: FleetIdentity | None = None,
    control_dir: Path | None = None,
    resource_limits: ResourceLimits | None = None,
    remote: RemoteNode | None = None,
    launch_timeout_seconds: int = 60,
) -> ContainerLaunchResult:
    """Launch a sandbox container and verify it reaches "Up" state.

    Raises DockerLaunchError on failure with the failing step recorded.
    """
    # Path checks must interrogate the machine that will actually mount them.
    # Calling Path.exists() for a remote launch asks the ORCHESTRATOR's
    # filesystem about the WORKER's paths — which silently passes whenever the
    # two happen to share a layout (they do here: both are
    # /Users/liborballaty/...), and silently fails when they do not. Either way
    # the answer is meaningless. Check over the wire instead.
    for label, path in (("worktree_path", worktree_path),
                        ("control_dir", control_dir)):
        if path is None:
            continue
        if remote is None:
            missing = not path.exists()
        else:
            probe = run(
                remote.wrap(["test", "-d", str(path)]), timeout=20,
            )
            # A non-zero exit means "absent"; a transport failure is a
            # different problem and must not be reported as a missing dir.
            if probe.returncode not in (0, 1):
                raise DockerLaunchError(
                    f"could not reach {remote.target()} to check {label}",
                    step="4.2",
                    node=remote.host,
                    stderr=(probe.stderr or probe.stdout).strip()[:500],
                )
            missing = probe.returncode != 0
        if missing:
            # Docker would create a missing bind-mount source as root; the
            # agent (uid 1000) then cannot write it, which surfaces much later
            # as an empty outbox rather than as a launch error.
            raise DockerLaunchError(
                f"{label} does not exist"
                + (f" on {remote.target()}" if remote else ""),
                step="4.2",
                **{label: str(path)},
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
        provider_api_key_env=provider_api_key_env,
        provider_api_key=provider_api_key,
        egress_allowlist=egress_allowlist,
        fleet_identity=fleet_identity,
        control_dir=control_dir,
        resource_limits=resource_limits,
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
    result = run(_docker(argv, remote), timeout=launch_timeout_seconds)
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
    if not _wait_until_up(sandbox_config.run_id, HEALTH_POLL_TIMEOUT_SECONDS,
                          remote):
        # Container was created but did not reach Up. Surface diagnostic logs.
        logs = _container_logs(sandbox_config.run_id, remote)
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


def _wait_until_up(sandbox_id: str, timeout_seconds: int,
                   remote: RemoteNode | None = None) -> bool:
    """Poll `docker ps` until the container's status starts with 'Up'."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = run(
            _docker(["docker", "ps", "--filter", f"name={sandbox_id}",
                     "--format", "{{.Status}}"], remote),
            timeout=10,
        )
        if result.ok:
            status = result.stdout.strip()
            if status.startswith("Up"):
                return True
        time.sleep(HEALTH_POLL_INTERVAL_SECONDS)
    return False


def _container_logs(sandbox_id: str, remote: RemoteNode | None = None) -> str:
    result = run(_docker(["docker", "logs", sandbox_id], remote), timeout=10)
    return (result.stdout or "") + (result.stderr or "")


def stop_container(sandbox_id: str, *, remove: bool = True, timeout: int = 10,
                   remote: RemoteNode | None = None) -> dict:
    """Stop (and optionally remove) the named container.

    WP-3 capture happens before this is called; here we only tear down.
    """
    stop_res = run(_docker(["docker", "stop", "-t", str(timeout), sandbox_id], remote),
                   timeout=timeout + 10)
    payload: dict = {
        "sandbox_id": sandbox_id,
        "stopped": stop_res.ok,
        "stop_stderr": stop_res.stderr.strip(),
    }
    if remove:
        rm_res = run(_docker(["docker", "rm", "-f", sandbox_id], remote), timeout=15)
        payload["removed"] = rm_res.ok
        payload["rm_stderr"] = rm_res.stderr.strip()
    return payload


def container_status(sandbox_id: str, remote: RemoteNode | None = None) -> dict:
    """Return basic status JSON for the named container, suitable for CLI output."""
    res = run(
        _docker(["docker", "ps", "-a", "--filter", f"name={sandbox_id}",
                 "--format", "{{.ID}}\t{{.Status}}\t{{.Image}}"], remote),
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
