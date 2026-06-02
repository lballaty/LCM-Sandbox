# Sandbox Orchestration & Secure Live Channel — Design Note

**Status:** Design, Phase 5 (or earlier if prioritized)
**Owner:** AIDevOps integration + Phase 2 entrypoint extensions
**Companion:** `SANDBOX-ORCHESTRATION.html` — visual diagrams of topology, lifecycle, and tool surface.

---

## Question this answers

Can AIDevOps communicate **live** with Claude inside a sandbox container — receiving progress, providing mid-run context, gating destructive ops on approval, capturing intermediate artifacts — without that channel becoming an exploit surface?

**Short answer:** Yes. The right architecture is **MCP Streamable HTTP with OAuth 2.1 audience-bound per-run bearer tokens, sandbox dialing out only, defense-in-depth at both the orchestrator and the container.** This matches the production pattern used by GitHub Actions, GitLab Runner, Temporal, Argo Workflows, and Buildkite — none of which let the sandbox listen for inbound traffic.

---

## The architecture in one paragraph

AIDevOps runs an **MCP Streamable HTTP server** that exposes a small, fixed set of orchestration tools (`report_progress`, `submit_intermediate_artifact`, `request_human_approval`, `get_more_context`, etc.). When a run starts, AIDevOps mints a short-lived **OAuth 2.1 bearer token bound to that `run_id`'s audience**, hands the token + endpoint URL to `lcm-sandbox create` as env vars, and the container's entrypoint writes them into Claude's `.mcp.json`. The in-container Claude **dials out** to the AIDevOps endpoint over HTTPS, presenting the token. AIDevOps validates the token's audience, the tool's authorization scopes, and every argument against the tool's JSON Schema + per-field validators before executing. The token expires when the run ends, and AIDevOps revokes it as a hard fail-safe.

---

## Why MCP (and not a custom HTTP API, gRPC, or message queue)

| Property | MCP Streamable HTTP | Custom HTTP / gRPC | Message Queue (Redis/NATS) |
| :------- | :------------------ | :------------------ | :------------------------- |
| Schema-validated tool surface | ✓ Built-in (JSON Schema) | ✗ DIY | ✗ DIY |
| Native Claude Code integration | ✓ via `.mcp.json` | ✗ requires custom prompting | ✗ requires custom prompting |
| Standard auth (OAuth 2.1, audience-bound) | ✓ spec-mandated | ✗ DIY | ✗ DIY |
| Tool discoverability (`tools/list`) | ✓ | ✗ | ✗ |
| Outbound-only (sandbox dials out) | ✓ | ✓ if designed that way | depends on broker |
| Standard transport (works through corp proxies) | ✓ HTTPS | ✓ HTTPS for HTTP; gRPC TBD | requires broker connectivity |

**The killer feature:** with MCP, Claude already knows how to call the tools. We don't have to write a prompt that teaches the agent the API contract — the schema *is* the contract. A custom API forces us to put the contract in the system prompt, which re-opens the prompt-injection attack surface.

**MCP's limitation:** the protocol was designed for *agent → tool* communication, not *orchestrator ↔ sandbox*. We're using it slightly off-label. This works because the orchestrator's tools are functionally a tool set the agent calls. We just have to be disciplined about authz, validation, rate-limiting, and not treating "JSON Schema validation" as "authorization" (it isn't — see the schema-is-not-authz section below).

---

## Topology — sandbox dials out, orchestrator never has inbound route

This is the consensus pattern across production orchestration systems:

```
                                                ╔═══════════════════╗
                                                ║   AIDevOps host   ║
                                                ║                   ║
   ┌──────────────────────────────────┐         ║  ┌──────────────┐ ║
   │ Sandbox container                │         ║  │ MCP server   │ ║
   │                                  │         ║  │ (HTTP +      │ ║
   │  ┌──────────────────────────┐    │ HTTPS   ║  │  OAuth 2.1)  │ ║
   │  │ Claude Code (MCP client) │────┼─────────┼──┼─▶ /tools/list│ ║
   │  └──────────────────────────┘    │  bearer ║  │   /tools/call│ ║
   │  - non-root aiagent              │         ║  └──────┬───────┘ ║
   │  - --cap-drop=ALL                │         ║         │         ║
   │  - --security-opt=no-new-privs   │         ║  ┌──────▼───────┐ ║
   │  - no docker.sock                │         ║  │ Workflow     │ ║
   │  - no --network=host             │         ║  │ engine + DB  │ ║
   └──────────────────────────────────┘         ║  └──────────────┘ ║
                                                ╚═══════════════════╝
```

Critical properties:
- **Container does not listen.** No published ports, no inbound network policy. Compromised agent has nowhere to be reached *from*.
- **Single egress endpoint.** Container's egress should be allowlisted (via iptables or a docker network) to ONLY the AIDevOps MCP URL + the Anthropic API endpoint. No general internet access.
- **HTTPS + Origin allowlist on the MCP server side** (MCP spec mandates this — prevents DNS rebinding).
- **Per-run audience-bound bearer token** (RFC 8707). A token leaked from one run cannot be replayed on another, and cannot be used against any other MCP server.

---

## Per-run token lifecycle

```
  AIDevOps starts run                Container exits                AIDevOps revokes
  → mint token                       (or timeout fires)             token
  → audience = mcp://aidevops/run-id ↓                              ↓
  → ttl = run timeout + 60s safety   token TTL expires naturally    fail-safe
  → scopes = run-specific toolset
       │
       ▼
  ┌─────────────────────────────┐
  │ Container env:              │
  │   MCP_SERVER_URL=https://…  │
  │   MCP_BEARER_TOKEN=…        │  (mounted as Docker secret,
  │                             │   never baked into image)
  │ Entrypoint writes:          │
  │   /home/aiagent/.claude/    │
  │     .mcp.json (via ${VAR}   │
  │     substitution — file is  │
  │     secret-free on disk)    │
  └─────────────────────────────┘
```

The token never lives on disk in plaintext beyond `/run/secrets/...` (tmpfs). `.mcp.json` references `${MCP_BEARER_TOKEN}` rather than embedding it.

---

## Canonical tool surface (what AIDevOps exposes)

Eight tools, each with a narrow purpose, JSON Schema-validated args, and explicit authorization at the server:

| Tool | Purpose | Args (sketch) |
| :--- | :------ | :------------ |
| `report_progress` | Heartbeat + structured progress | `phase: string, percent: int 0-100, message: string` |
| `submit_intermediate_artifact` | Mid-run output (logs, diff, screenshot) | `kind: enum, content: bytes ≤ 10MB, metadata: object` |
| `request_human_approval` | Gate a destructive op; blocks until human responds | `action: string, rationale: string, blast_radius: enum, timeout_seconds: int ≤ 3600` |
| `get_more_context` | Pull extra context AIDevOps may have queued | `query: string, kind: enum` |
| `query_workflow_state` | Current plan/step/dependencies | `(no args)` |
| `log_event` | Structured telemetry | `level: enum, event: string, fields: object` |
| `register_finding` | Record an issue/risk discovered mid-run | `severity: enum, title: string, evidence: object` |
| `notify_completion` | Terminal signal with summary | `status: enum, summary: string` |

**Idempotency:** every tool accepts an optional `client_event_id` (client-generated UUID). AIDevOps deduplicates retries by `(run_id, client_event_id)`. This makes the channel safe under transient network errors and disconnect/reconnect.

**Rate limits:** AIDevOps enforces per-run token-bucket limits (e.g., 100 calls/minute, 1000 calls/run). Burst above the limit returns `429`. The agent's prompt will be told the limits exist so it self-paces.

---

## Schema-is-not-authz: the layered validation stack

JSON Schema defeats *structural* injection (wrong types, oversized fields, undefined enums). It does **not** defeat *semantic* injection — a structurally-valid `submit_intermediate_artifact{path: "../../etc/shadow"}` still passes the schema. AIDevOps must layer:

1. **Schema validation** (transport layer — MCP does this)
2. **Per-method authorization** — does this token's scope allow this tool?
3. **Per-field validators**:
   - Path arguments: canonicalize, reject `..`, enforce prefix allowlist (`/workspace/...` only)
   - Size caps on byte arrays (≤ 10MB intermediate artifact)
   - Enum allowlisting on free-string fields that should be enums
   - Length caps on free strings (e.g. `message ≤ 4096 chars`)
4. **Rate limiting + quota** per `run_id`
5. **Audit log of every call** with full args (redacted secrets) for incident response
6. **Honest tool descriptions** (the agent reads them) — describe what each tool *does* and what it *can't be used for*

Treat schema validation as input canonicalization. The actual security boundary is the combination of authz + per-field validators + rate limits.

---

## Container hardening (beyond IPC)

These come from Agent B's research — production-standard. The current STEP 4.5 entrypoint handles ACLs; the docker run flags need additions:

```bash
docker run \
  --rm \
  --name <sandbox_id> \
  --user aiagent \
  --read-only \
  --tmpfs /tmp:rw,nosuid,nodev,size=512m \
  --tmpfs /run/secrets:rw,nosuid,nodev,size=4m \
  --cap-drop=ALL \
  --security-opt=no-new-privileges \
  --security-opt=seccomp=default \
  --pids-limit=512 \
  --memory=4g \
  --cpus=2 \
  --network <run-scoped-network> \
  -v <worktree>:/workspace \
  -v <repo>/.git:/workspace/.git-parent:ro \
  -v <token-file>:/run/secrets/lcm-run-token:ro \
  -e MCP_SERVER_URL=<endpoint> \
  -e MCP_BEARER_TOKEN_FILE=/run/secrets/lcm-run-token \
  lcm-dev-agent:latest \
  /entrypoint.sh
```

Critical:
- `--cap-drop=ALL` + `--security-opt=no-new-privileges` — removes the runtime ability to escalate even if the non-root user is compromised
- `--read-only` root filesystem + tmpfs for the writable bits — limits persistence vectors
- `<run-scoped-network>` — a docker network with egress allowlisted to only the AIDevOps endpoint + Anthropic API (via iptables or docker network policy)
- **No** `--network=host`, **no** `--ipc=host`, **no** `--privileged`, **no** docker.sock mount

**Defense-in-depth assumption:** the container *will* be compromised someday. The IPC design ensures compromise gains only "send messages to the orchestrator within the declared tool surface" — not "scan the network", "talk to docker.sock", or "exfiltrate to arbitrary URLs."

---

## How this slots into the existing CLI (small additions only)

Three new CLI flags on `lcm-sandbox create`:

```
--mcp-endpoint <url>           # AIDevOps MCP server URL (HTTPS)
--mcp-token-file <path>        # path to a file containing the per-run bearer token
--egress-allowlist <hosts>     # comma-separated, forwarded to docker network config
```

These are forwarded into the container as env vars / volume mounts. The entrypoint, between STEP 4.5.7 and 4.5.8 (already documented in `SANDBOX-AGENT-CONFIG.md`), writes the MCP server entry into `/home/aiagent/.claude/.mcp.json`:

```json
{
  "mcpServers": {
    "aidevops": {
      "type": "http",
      "url": "${MCP_SERVER_URL}",
      "headers": {
        "Authorization": "Bearer ${MCP_BEARER_TOKEN}"
      },
      "alwaysLoad": true
    }
  }
}
```

If `--mcp-endpoint` is not provided (user-CLI path), no `.mcp.json` is written and the agent runs without the back-channel. Same code, two trigger paths.

**Known issue to verify in installed version:** GitHub anthropics/claude-code #28293 reports that custom headers from `.mcp.json` may not be forwarded on subsequent POSTs after the initial connection. If still open: use a small stdio-MCP shim inside the container that holds the token in memory and proxies to HTTPS. This is implementation detail, not architecture.

---

## Two paths — user-CLI vs AIDevOps-orchestrated

Same machinery, different lifecycle owner. See the table in `SESSION-HANDOFF.md` for the full side-by-side; the key delta:

| Concern | User-CLI | AIDevOps-orchestrated |
| :------ | :------- | :-------------------- |
| Trigger | Human types CLI command | AIDevOps workflow engine calls CLI as subprocess |
| Mid-run channel | None (or user can opt in to their own MCP endpoint) | AIDevOps MCP server, per-run audience-bound token |
| Approval gates | N/A — human at terminal | `request_human_approval` tool, workflow gate, 1-hour timeout |
| Authentication | User's `ANTHROPIC_API_KEY` | Per-run Anthropic key minted by AIDevOps + per-run AIDevOps bearer |
| Concurrency | One at a time per human | N concurrent runs, fanned out by workflow engine |
| Artifact destination | Local JSON | `agent_artifacts` table + S3 + audit log |
| Cleanup | Manual | PR-merged webhook → auto-cleanup |

Both paths share Phases 0–6 byte-for-byte. The AIDevOps path is **a wrapper around the CLI**, not a parallel implementation.

---

## What needs to be true before this ships

### Verify in the installed Claude Code version
- [ ] `.mcp.json` `headers.Authorization: Bearer ${VAR}` is forwarded on every POST (issue #28293 status)
- [ ] `elicitation/create` and `notifications/*` server-initiated messages reach Claude (verify with a test MCP server)
- [ ] `enableAllProjectMcpServers: true` actually skips the project-MCP approval (we covered this assumption earlier)

### Build on the AIDevOps side
- [ ] MCP server implementing the 8 tools above with full layered validation
- [ ] OAuth 2.1 token issuer with audience binding and revocation endpoint
- [ ] Docker network policy module that allowlist's per-run egress to MCP endpoint + Anthropic API only
- [ ] Audit log sink with per-call structured records
- [ ] Workflow gates that consume `request_human_approval` calls

### Build on the lcm-sandbox side (small)
- [ ] `--mcp-endpoint`, `--mcp-token-file`, `--egress-allowlist` flags on `create`
- [ ] Entrypoint step to write `.mcp.json` when MCP endpoint is provided
- [ ] Docker run flags expanded with `--cap-drop=ALL --security-opt=no-new-privileges --read-only`
- [ ] Smoke test that probes the MCP channel during bootstrap

### Operationally
- [ ] Decide whether AIDevOps runs the MCP server in-process or as a separate service
- [ ] Decide whether the run-scoped network is a docker network with egress iptables or a more involved CNI/policy approach
- [ ] Decide audit log retention period and storage backend

---

## Open questions / things to validate

1. **MCP server-pushed notifications** — Agent A flagged that true server→client push is not in the MCP spec; closest equivalents (`sampling/createMessage`, `elicitation/create`) have lagging implementation support. **Recommendation:** rely on agent-polled `get_more_context()` for now, treat push as a future enhancement.

2. **Custom-header forwarding bug (#28293)** — must be tested before we commit to header-based auth. If still buggy, the workaround is a stdio proxy.

3. **Per-run docker network egress allowlisting** — needs concrete spike: is this achievable with vanilla Docker + iptables, or do we need a CNI plugin? Affects what hosts can run sandboxes.

4. **Rate limit calibration** — 100/min and 1000/run are starting guesses. Need to derive from a real workload.

5. **Token revocation propagation** — when AIDevOps revokes a token, how fast does the in-container Claude notice? MCP doesn't define this; we'd need a server-side token check on every call (already implied by stateless OAuth) and observe what happens on 401.

---

## References

- MCP Specification 2025-06-18 — Transports: https://modelcontextprotocol.io/specification/2025-06-18/basic/transports
- MCP Specification — Authorization: https://modelcontextprotocol.io/specification/draft/basic/authorization
- Claude Code MCP docs: https://code.claude.com/docs/en/mcp
- GitHub anthropics/claude-code #28293: https://github.com/anthropics/claude-code/issues/28293
- OWASP Docker Security Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Docker_Security_Cheat_Sheet.html
- Black Hat USA 2019 — Compendium of Container Escapes: https://i.blackhat.com/USA-19/Thursday/us-19-Edwards-Compendium-Of-Container-Escapes-up.pdf
- CVE-2024-21626 (Leaky Vessels): https://nvd.nist.gov/vuln/detail/CVE-2024-21626
- GitHub Actions self-hosted runners (outbound-only pattern): https://docs.github.com/en/actions/reference/runners/self-hosted-runners
