# Phase 5 Prerequisite — GitHub Issue Verification

**File:** `docs/PHASE-5-PREREQS-VERIFICATION.md`
**Description:** Resolution status of the two GH `anthropics/claude-code` issues that gate the Phase 5 AIDevOps integration design (see `SANDBOX-ORCHESTRATION.md`). Required by `PLAN-REMAINING-WORK.md` Phase 1, Tasks 1.2 + 1.3.
**Author:** Libor Ballaty <libor@arionetworks.com>
**Created:** 2026-06-16
**Last Updated:** 2026-06-16
**Last Updated By:** Libor Ballaty

---

## Environment

| Item | Value |
| :--- | :--- |
| Installed Claude Code version | `2.1.145 (Claude Code)` |
| Date of verification | 2026-06-16 |
| Source of truth | `gh issue view <number> --repo anthropics/claude-code` |

---

## Issue #28293 — Custom headers from `.mcp.json` not forwarded on MCP tool call POSTs

### Repository state (read 2026-06-16)

- **State:** `CLOSED`
- **State reason:** `NOT_PLANNED`
- **Closed at:** 2026-04-23
- **Title:** "Custom headers from .mcp.json not forwarded on MCP tool call POST requests"

### What the bug actually is

Custom HTTP headers (e.g. `X-API-KEY`, `x-mcp-api-key`) defined under the `headers:` key in a project's `.mcp.json` are persisted correctly but are **not forwarded on the tool-call POST requests at runtime**. Claude Code falls back to OAuth and the call fails (HTTP 403 / `Invalid OAuth error response`).

Last user report in the thread is on **v2.1.83** (closed before our installed `v2.1.145`).

### Workaround documented in the issue thread (verified by multiple reporters)

Adding the MCP server via the CLI with **user** scope persists the config to `~/.claude.json`, and headers attached there **are** forwarded correctly at runtime:

```bash
claude mcp add my-server "https://example.com/mcp/sse" \
  -t sse -s user \
  -H "X-API-KEY: my-key"
```

(Equivalent for HTTP transport: `-t http`.) The user scope (`~/.claude.json`) functions; the project scope (`.mcp.json`) is the broken path.

### Verdict

**Still broken in the `.mcp.json` form** — closed not-planned, no fix shipped. The workaround is to **bootstrap the MCP server via `claude mcp add -s user`** at sandbox start instead of committing a `.mcp.json` to the worktree.

### Impact on Phase 5 design (`SANDBOX-ORCHESTRATION.md`)

The bearer-in-header authentication pattern in §2 of the orchestration design is **viable** but the wiring needs to change:

- ❌ Do **not** ship `.mcp.json` as a worktree-committed file with the AIDevOps endpoint + bearer header.
- ✅ The container entrypoint must call `claude mcp add aidevops <MCP_SERVER_URL> -t http -s user -H "Authorization: Bearer ${MCP_TOKEN}"` before launching the agent.
- ✅ The token rotation story is unchanged — the user-scope config is per-container by definition (no host bleed), and the entrypoint re-runs the add command on every container start.

This is a small adjustment to the design and does not block Phase 5.

---

## Issue #36665 — feat: MCP server push notifications (unsolicited messages to client)

### Repository state (read 2026-06-16)

- **State:** `CLOSED`
- **State reason:** `NOT_PLANNED`
- **Closed at:** 2026-05-23
- **Title:** "feat: MCP server push notifications (unsolicited messages to client)"

### Why closed

Anthropic consolidated tracking under **#35072** ("push MCP notifications into the active session"). The feature remains unimplemented; the original ask is recognized and tracked elsewhere.

### Community workarounds documented in the thread

Multiple commenters describe the same constraint and report two viable workarounds:

1. **Blocking long-poll tool calls** (the agent's own MCP call blocks for ≤ 55s, returning instantly when a message arrives — round-trip latency drops to ~5–7s).
2. **PostToolUse hooks driven by a Socket Mode listener** — used by `claude-code-slack-notifier`.

### Verdict

**Server push remains unsupported** — closed not-planned, tracked under #35072. The polling-fallback path that `SANDBOX-ORCHESTRATION.md` already contemplates is the correct design.

### Impact on Phase 5 design

No change required. The orchestration design's `get_more_context()` heartbeat + polling pattern is the recommended approach for the foreseeable future. If/when #35072 ships, push can be added as an optional optimization on top of the existing polling fallback.

---

## Combined verdict

Both gates resolve favorably enough to unblock Phase 5 implementation:

- #28293 — workaround validated; design adjustment is small.
- #36665 — no change required; existing fallback is the right path.

Re-check #35072 quarterly to opportunistically pick up server push when (if) it ships.
