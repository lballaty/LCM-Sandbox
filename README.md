# LCM-Sandbox

Globally-available Python CLI tool for creating isolated execution environments for Claude Code agents in `bypassPermissions` mode.

## Overview

**LCM-Sandbox** enables safe, bounded agent execution by:
- Creating isolated git worktrees on the host
- Running agents in Docker containers with restricted OS permissions
- Enforcing file-level access control (only specific directories writable)
- Blocking git pushes to origin (commits only, pre-merge review required)
- Capturing and auditing all artifacts (stdout, commits, diffs, logs)

Perfect for:
- Local developer testing with agents in permission-bypass mode
- AIDevOps integration for high-risk automated workflows
- Reproducible, auditable agent execution with full artifact capture

---

## Quick Start

### Installation

```bash
pip install lcm-sandbox
```

### Basic Usage

```bash
# Create a sandbox
lcm-sandbox create \
  --repo /path/to/repo \
  --branch feature/my-branch \
  --allowed-paths '{"write":["src/"],"read":["*"]}' \
  --timeout 120

# Check status
lcm-sandbox status --sandbox-id sandbox-run_xxx-...

# Clean up after merge
lcm-sandbox cleanup --sandbox-id sandbox-run_xxx-...
```

---

## Architecture & Design

### Three-Layer Isolation

1. **VM Layer** (Colima): Host docker daemon isolated from developer machine
2. **Container Layer** (Docker): Ubuntu 24.04 with restricted network
3. **OS User Layer** (aiagent): Non-sudo user with file ACLs enforcing scope

### Documentation

- **[SANDBOX-ARCHITECTURE.md](./SANDBOX-ARCHITECTURE.md)** — High-level workflows, container internals, data flows, threat model
- **[SANDBOX-DETAILED-FLOW.md](./SANDBOX-DETAILED-FLOW.md)** — Phase-by-phase execution with exact bash commands, error handling, verification steps
- **[IMPLEMENTATION-PLAN.md](./IMPLEMENTATION-PLAN.md)** — Build roadmap, component breakdown, timeline (4-5 weeks)
- **[AGENT-INSTRUCTIONS.md](./AGENT-INSTRUCTIONS.md)** — Phased delivery guide for autonomous agent implementation
- **[SANDBOX-AGENT-CONFIG.md](./SANDBOX-AGENT-CONFIG.md)** + **[.html](./SANDBOX-AGENT-CONFIG.html)** — In-sandbox agent permission profile (silencing every prompt path)
- **[SANDBOX-IMAGE-TOOLCHAIN.md](./SANDBOX-IMAGE-TOOLCHAIN.md)** — Phase 2 Dockerfile design (universal image, ~1 GB target)
- **[SANDBOX-ORCHESTRATION.md](./SANDBOX-ORCHESTRATION.md)** + **[.html](./SANDBOX-ORCHESTRATION.html)** — Secure live IPC channel (MCP Streamable HTTP, OAuth 2.1 per-run tokens)
- **[SANDBOX-FLOWS.html](./SANDBOX-FLOWS.html)** — End-to-end mermaid flows: direct CLI / AIDevOps UI / AIDevOps programmatic, plus cleanup, failure, and state machine
- **[SESSION-HANDOFF.md](./SESSION-HANDOFF.md)** — Resume-from-here doc for the next session

### Key Concepts

**Sandbox ID**: Auto-generated identifier linking to execution context
- Example: `sandbox-run_def456uvw-20260530T140523Z`
- Allows tracking and audit trail without manual naming

**Allowed Paths**: JSON configuration specifying writable directories
```json
{
  "write": ["src/", "tests/"],
  "read": ["*"]
}
```

**Worktree Reuse**: Single worktree persists across multi-step plans
- Step 1 creates/resets worktree, Step 2 reuses same worktree with commits visible
- Final worktree has accumulated commits from all steps

**Git Constraints**:
- ✅ Commits allowed (reviewed before merge)
- ❌ Pushes blocked (pre-push hook)

---

## Use Cases

### Local Development

Developer wants to test an agent refactoring with `bypassPermissions` mode on a specific directory:

```bash
lcm-sandbox create \
  --repo ~/openrouter-agent \
  --branch feature/agent-refactor \
  --allowed-paths '{"write":["src/agents/"],"read":["*"]}' \
  --command "claude --permission-mode bypassPermissions"
```

Agent modifies only `src/agents/`, commits are captured, can be reviewed before merge.

### AIDevOps Integration

High-risk workflow step (e.g., automatic refactoring) pauses for approval, then:

```python
from lcm_sandbox import SandboxRunner

result = await sandbox_runner.create(
    repo_path="/path/to/repo",
    branch_name="feature/auto-refactor",
    allowed_paths={"write": ["src/"], "read": ["*"]},
    plan_id="plan_abc123",
    run_id="run_def456",
    timeout_minutes=120
)

# Returns: { sandbox_id, status, exit_code, num_commits, ... }
```

Artifacts stored in DB for audit trail.

---

## Status

_Last reconciled: 2026-06-16_

| Phase | Scope | Status |
| :---- | :---- | :----- |
| 1 | Core CLI + Phases 0–3 (preflight, worktree, sync, docker image check) | ✅ **Done & committed** — 43 Phase-1 tests passing |
| 2 | Docker image + entrypoint (STEP 4.5) | 🟡 **Partially implemented & committed** — `scripts/Dockerfile.hermes` + `scripts/entrypoint.sh` + build helper in main; git-hooks / rm-shim / smoke-test / agent-profile templates still missing |
| 3 | Artifact capture + cleanup | ⬜ Not started |
| 4 | Container launch + `launch`/`stop`/`status` CLI commands | 🟡 **Implemented & committed** — `lcm_sandbox/core/docker_launcher.py` in main; hardening flags + `--egress-allowlist` still to verify |
| 5 | AIDevOps integration + live MCP channel | ⬜ Designed (see orchestration + flows docs); blocked on verifying GH `claude-code` issues #28293 + #36665 |
| WP-8 | HERMES persona renderer + capturer (scope added since original plan) | 🟡 Implemented & committed; 7 tests currently red under local Privoxy proxy interception |
| Dev-sandbox template (manual flow) | Image + launch/setup/verify/stop scripts + host CLI wrapper + runbook + `/dev-sandbox` skill | ✅ **Verified end-to-end 2026-06-16** — single allowlisted `dev-sandbox` CLI orchestrates `setup-dev-sandbox.sh` (build), `run-dev-sandbox.sh` (launch, label-tagged), `verify-dev-sandbox.sh` (11-check suite), `stop-dev-sandbox.sh` (image+label guarded). All 11 verify checks PASS. |

Test suite (current tree): **50 passing / 7 failing / 1 skipped**. The 7 failures are all `test_persona_render_capture.py` hitting an interception by the local Privoxy proxy; the skipped test waits on a locally-built `lcm-hermes-agent:latest` image.

See [SESSION-HANDOFF.md](./SESSION-HANDOFF.md) and [IMPLEMENTATION-PLAN.md](./IMPLEMENTATION-PLAN.md) for resume-from-here details.

---

## Contributing

This is an experimental project. For questions or contributions, refer to the architecture docs and detailed flow documents.

---

## License

TBD
