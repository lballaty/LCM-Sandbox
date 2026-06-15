# Sandbox Implementation Plan

This document specifies what to build, in what order, and how.

---

## Overview

**Goal**: Build a globally-available Python CLI tool (`lcm-sandbox`) that:
1. Takes an approved plan + sandbox config
2. Creates/syncs a worktree
3. Launches a Docker container with file ACLs
4. Runs an agent inside (no internal safety checks, bounded by OS)
5. Captures artifacts (commits, diffs, logs)
6. Returns results for user review before merge

**Deliverables**:
- Python package (`lcm-sandbox`) installable via pip
- Docker image (`lcm-dev-agent`) buildable from Dockerfile
- Shell scripts for Docker entrypoint and git hooks
- Integration hooks for AIDevOps (optional Phase 2)

---

## Component Breakdown

### 1. Python CLI Tool (`lcm_sandbox` package)

**Location**: Separate repo or in `scripts/lcm_sandbox/`

**Core modules**:

```
lcm_sandbox/
├── __init__.py
├── cli.py                    # Main CLI entry point (argparse)
├── commands/
│   ├── __init__.py
│   ├── create.py            # Main command: create sandbox
│   ├── cleanup.py           # Cleanup: remove worktree after merge
│   └── status.py            # Status: list running/recent sandboxes
├── core/
│   ├── __init__.py
│   ├── config.py            # Config parsing, validation
│   ├── preflight.py         # Phase 0: Pre-flight checks
│   ├── worktree.py          # Phase 1: Worktree creation/reset
│   ├── sync.py              # Phase 2: Sync to latest main
│   ├── docker_builder.py    # Phase 3: Image preparation
│   ├── docker_launcher.py   # Phase 4: Container launch
│   └── artifact_capture.py  # Phase 6: Capture results
├── models/
│   ├── __init__.py
│   ├── sandbox_config.py    # Pydantic models for config
│   └── artifact.py          # Result models
├── utils/
│   ├── __init__.py
│   ├── git.py               # Git operations (fetch, rebase, etc)
│   ├── docker.py            # Docker operations (run, logs, etc)
│   ├── shell.py             # Shell execution helpers
│   └── logger.py            # Structured logging
└── tests/
    ├── test_preflight.py
    ├── test_worktree.py
    ├── test_sync.py
    ├── test_docker.py
    └── test_e2e.py
```

**Dependencies**:
```
# setup.py
install_requires=[
    'pydantic>=2.0',          # Config validation
    'click>=8.0',             # CLI framework (alternative: argparse)
    'docker>=6.0',            # Docker SDK
    'gitpython>=3.1',         # Git operations
    'requests>=2.28',         # HTTP (for S3, etc)
    'pyyaml>=6.0',            # YAML parsing
]
```

**Entry point** (in setup.py):
```python
entry_points={
    'console_scripts': [
        'lcm-sandbox=lcm_sandbox.cli:main',
    ]
}
```

---

### 2. Docker Image & Scripts

**Dockerfile** (in repo):
```dockerfile
FROM ubuntu:24.04

ARG ALLOWED_PATHS='[]'

# Install system deps
RUN apt-get update && apt-get install -y \
    git curl wget ca-certificates \
    nodejs npm python3 \
    && rm -rf /var/lib/apt/lists/*

# Install Claude Code & Codex
RUN npm install -g @anthropic-ai/claude-code @openai/codex

# Create restricted group and user
RUN groupadd -r agentgroup && \
    useradd -r -m -g agentgroup -s /bin/bash aiagent

# Copy entrypoint scripts
COPY scripts/docker-entrypoint.sh /entrypoint.sh
COPY scripts/docker-git-hooks.sh /git-hooks.sh
RUN chmod +x /entrypoint.sh /git-hooks.sh

# Create workspace
RUN mkdir -p /workspace && chown -R aiagent:agentgroup /workspace

WORKDIR /workspace
ENV ALLOWED_PATHS=${ALLOWED_PATHS}
USER root

ENTRYPOINT ["/entrypoint.sh"]
CMD ["/bin/bash"]
```

**Scripts**:
```
scripts/
├── docker-entrypoint.sh     # Phase 4.5: Container startup (bash)
└── docker-git-hooks.sh      # Pre-push hook installer (bash)
```

**docker-entrypoint.sh** (pseudocode):
```bash
#!/bin/bash
set -e

# Phase 4.5.1: Parse & validate env vars
[ -n "$PLAN_ID" ] || exit 1
[ -n "$SANDBOX_ID" ] || exit 1
[ -n "$ALLOWED_PATHS" ] || exit 1

# Phase 4.5.2-4.5.7: Permission setup, git config, hooks, manifest
chmod -R 644 /workspace
parse_and_apply_allowed_paths "$ALLOWED_PATHS"
git config --global safe.directory /workspace
install_pre_push_hook "/workspace/.git/hooks/pre-push"
write_sandbox_manifest "/workspace/.sandbox-manifest.json"

# Phase 4.5.8-4.5.9: Switch user, verify git
su - aiagent <<'EOF'
cd /workspace
git status  # Verify working
export PS1="[sandbox] $ "
/bin/bash -i
EOF
```

**docker-git-hooks.sh** (pseudocode):
```bash
#!/bin/bash
cat > "$1" <<'EOF'
#!/bin/bash
echo "ERROR: Pushing to origin is not allowed in sandbox"
exit 1
EOF
chmod +x "$1"
```

---

### 3. Configuration Files

**`.claude/settings.json`** (project-level, optional):
```json
{
  "sandbox": {
    "colima_profile": "LCM-Dev",
    "timeout_minutes": 120,
    "archive_location": "s3://artifacts/sandboxes/",
    "enable_auto_cleanup": true
  }
}
```

**`setup.py`** (Python package):
```python
from setuptools import setup, find_packages

setup(
    name='lcm-sandbox',
    version='0.1.0',
    description='Isolated sandbox execution for Claude Code agents',
    packages=find_packages(),
    install_requires=[
        'pydantic>=2.0',
        'click>=8.0',
        'docker>=6.0',
        'gitpython>=3.1',
        'requests>=2.28',
        'pyyaml>=6.0',
    ],
    entry_points={
        'console_scripts': [
            'lcm-sandbox=lcm_sandbox.cli:main',
        ]
    },
    python_requires='>=3.9',
)
```

---

## File Structure

```
openrouter-agent-sandbox/
├── lcm_sandbox/                    # Python package
│   ├── __init__.py
│   ├── cli.py
│   ├── commands/
│   ├── core/
│   ├── models/
│   ├── utils/
│   └── tests/
├── scripts/
│   ├── docker-entrypoint.sh        # Phase 4.5
│   ├── docker-git-hooks.sh         # Git pre-push hook
│   └── install.sh                  # Pip install + symlink (optional)
├── Dockerfile                       # Container image
├── setup.py                         # Package config
├── setup.cfg                        # Package metadata
├── pyproject.toml                   # Modern Python config
├── requirements.txt                 # Dev dependencies
├── requirements-dev.txt             # Test dependencies
├── IMPLEMENTATION-PLAN.md           # This document
├── SANDBOX-ARCHITECTURE.md          # Architecture overview
├── SANDBOX-DETAILED-FLOW.md         # Step-by-step flow
├── tests/                           # Integration tests
│   ├── test_e2e_sandbox.py         # End-to-end test
│   └── fixtures/                    # Test repos, configs
├── docs/
│   ├── USAGE.md                     # CLI usage guide
│   ├── API.md                       # Python API docs
│   ├── AIDEVOPS-INTEGRATION.md      # AIDevOps job handler
│   └── TROUBLESHOOTING.md           # Common issues
└── examples/
    ├── example_sandbox_config.json  # Example config
    └── example_plan.json            # Example plan
```

---

## Implementation Phases

### Phase 1: Core Python CLI (Week 1-2)

**Deliverable**: `lcm-sandbox` CLI can create a sandbox

**Tasks**:

1. **Setup project structure**
   - Create package layout
   - setup.py with dependencies
   - requirements-dev.txt for testing
   - pytest configured

2. **Implement Phase 0: Pre-flight checks** (`core/preflight.py`)
   - Verify repo exists and is git repo
   - Verify branch exists
   - Verify Colima profile running
   - Verify Docker accessible
   - Config validation
   - No duplicate sandboxes

3. **Implement Phase 1: Worktree** (`core/worktree.py`)
   - Determine worktree path
   - Create new worktree OR reset existing
   - Sub-steps: verify origin/main, ensure branch tracking, create/reset

4. **Implement Phase 2: Sync** (`core/sync.py`)
   - Fetch from origin
   - Check sync status (in_sync/behind/ahead/diverged)
   - Rebase if needed
   - Verify clean state
   - Log baseline

5. **Implement Phase 3: Docker image prep** (`core/docker_builder.py`)
   - Check if image exists
   - Build if needed
   - Verify functional

6. **CLI scaffolding** (`cli.py`)
   - Main entry point (click or argparse)
   - `lcm-sandbox create` command
   - Config parsing
   - Error handling

7. **Testing**
   - Unit tests for each core module
   - Mock git/docker where needed
   - Test error paths

**Success criteria**:
```bash
$ lcm-sandbox create --repo /path/to/repo --allowed-paths '{"write":["src/"]}'
# Creates worktree, syncs, builds image, and prints status
```

---

### Phase 2: Docker Integration (Week 2-3)

**Deliverable**: Container launches and agent can work inside

**Tasks**:

1. **Finalize Dockerfile**
   - Base image, deps, tools
   - ALLOWED_PATHS arg
   - User setup (aiagent)

2. **Implement entrypoint scripts**
   - `docker-entrypoint.sh` (all Phase 4.5 steps)
   - `docker-git-hooks.sh` (pre-push hook)
   - Permission setup, git config, manifest

3. **Implement Phase 4: Container launch** (`core/docker_launcher.py`)
   - Prepare env vars
   - Prepare mounts
   - Launch container
   - Verify running
   - Monitor execution (timeout, exit)

4. **Testing**
   - Integration test: launch container, verify permissions
   - Test file ACLs (writable vs read-only)
   - Test git operations inside
   - Test timeout handling

**Success criteria**:
```bash
$ lcm-sandbox create ... --command "echo hello"
# Container launches, command runs, exits cleanly
```

---

### Phase 3: Artifact Capture (Week 3)

**Deliverable**: Results captured and stored

**Tasks**:

1. **Implement Phase 6: Artifact capture** (`core/artifact_capture.py`)
   - Capture exit code, logs, git state
   - Calculate stats
   - Archive artifacts
   - Validate no scope violations
   - Generate JSON result

2. **Implement cleanup command** (`commands/cleanup.py`)
   - Remove worktree after merge
   - Archive final artifacts
   - Update DB status

3. **Integration with storage**
   - S3 upload (or local filesystem for MVP)
   - DB schema (or JSON files for MVP)
   - Audit logging

4. **Testing**
   - Verify artifacts captured correctly
   - Test diff generation
   - Test scope validation

**Success criteria**:
```bash
$ lcm-sandbox create ...
# Returns JSON with:
# - sandbox_id, status, exit_code
# - num_commits, files_modified, lines_added
# - artifacts_url, violations
```

---

### Phase 4: Documentation & Testing (Week 4)

**Deliverable**: Full docs, E2E tests, ready for use

**Tasks**:

1. **Documentation**
   - USAGE.md: CLI examples
   - API.md: Python module usage
   - AIDEVOPS-INTEGRATION.md: How AIDevOps calls it
   - TROUBLESHOOTING.md: Common issues

2. **End-to-end test**
   - Create test repo
   - Run full sandbox execution
   - Verify artifacts match expectations

3. **Packaging**
   - Publish to PyPI (or internal repo)
   - Or provide install script

4. **Examples**
   - Example sandbox configs
   - Example plans
   - Example AIDevOps job handler code

---

### Phase 5: AIDevOps Integration (Optional, Week 5)

**Deliverable**: AIDevOps can spawn sandboxes via job handler

**Tasks**:

1. **Job handler in AIDevOps**
   - New handler: `JOB_HANDLERS['platform:sandbox-run']`
   - Subprocess spawn `lcm-sandbox create ...`
   - Capture output, store artifact
   - Audit logging

2. **Approval gate integration**
   - High-risk plans pause for approval
   - On approval, spawn sandbox

3. **Webhook cleanup**
   - Detect merge to main
   - Trigger worktree cleanup

---

## Key Implementation Details

### Error Handling Strategy

Every phase has a clear error exit:

```python
# Example pattern
class SandboxError(Exception):
    pass

class PrefailCheckError(SandboxError):
    pass

try:
    preflight.validate_config(config)
except PrefailCheckError as e:
    logger.error(f"Pre-flight check failed: {e}")
    return {"status": "error", "phase": 0, "reason": str(e)}
```

### Logging

Use structured logging throughout:

```python
import logging
logger = logging.getLogger(__name__)

logger.info("action", extra={
    "phase": 0,
    "step": 1,
    "repo_path": repo_path,
    "timestamp": datetime.now().isoformat()
})
```

### Testing Approach

```python
# tests/test_preflight.py
import pytest
from unittest.mock import patch, MagicMock

def test_preflight_repo_missing():
    config = {"repo_path": "/nonexistent"}
    with pytest.raises(PrefailCheckError):
        preflight.validate_config(config)

def test_preflight_all_pass():
    config = {"repo_path": "/real/repo", ...}
    with patch('core.preflight.subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = preflight.validate_config(config)
        assert result is None  # No error
```

### Configuration Model

```python
# models/sandbox_config.py
from pydantic import BaseModel, Field
from typing import Dict, List

class AllowedPaths(BaseModel):
    write: List[str]
    read: List[str]

class SandboxConfig(BaseModel):
    plan_id: str
    run_id: str
    repo_path: str
    branch_name: str
    allowed_paths: AllowedPaths
    timeout_minutes: int = Field(ge=15, le=480)
    colima_profile: str = "LCM-Dev"

    class Config:
        validate_assignment = True
```

---

## CLI Interface (Final)

```bash
# Create a sandbox
lcm-sandbox create \
  --repo /path/to/repo \
  --branch feature/my-feature \
  --allowed-paths '{"write":["src/","tests/"],"read":["*"]}' \
  --timeout 120 \
  --colima-profile LCM-Dev

# Expected output (JSON):
# {
#   "sandbox_id": "sandbox-run_xxx-20260531T120000Z",
#   "status": "created",
#   "worktree_path": "/path/to/.sandbox-worktrees/...",
#   "ready": true
# }

# Check status
lcm-sandbox status --sandbox-id sandbox-run_xxx-...

# Clean up after merge
lcm-sandbox cleanup --sandbox-id sandbox-run_xxx-...
```

---

## Testing Strategy

### Unit Tests
- Each module tested in isolation
- Mock git/docker operations
- Test error paths and validation

### Integration Tests
- Create real worktree, sync, launch container
- Use test repo or fixture
- Clean up after test

### E2E Test
- Full sandbox execution
- Verify artifacts captured correctly
- Compare against known baseline

**Running tests**:
```bash
pytest lcm_sandbox/tests/
pytest --integration lcm_sandbox/tests/  # Only integration tests
pytest --e2e lcm_sandbox/tests/          # Only E2E tests
```

---

## Deployment & Distribution

### Option 1: PyPI
```bash
# Build
python setup.py sdist bdist_wheel

# Upload
twine upload dist/*

# User installs
pip install lcm-sandbox
```

### Option 2: Local pip install (for MVP)
```bash
# Clone repo
git clone <repo>
cd openrouter-agent-sandbox

# Install in editable mode
pip install -e .

# Binary available globally
lcm-sandbox --help
```

### Option 3: Homebrew (macOS)
```bash
# Formula
brew install lcm-sandbox
```

---

## Dependencies & Prerequisites

### Host Machine (macOS)
- Python 3.9+
- git 2.25+
- Colima (already installed)
- Docker (via Colima)

### Container
- Ubuntu 24.04
- Node.js, npm
- Claude Code CLI
- ChatGPT Codex
- git, python3, curl, wget

### AIDevOps (if integrating)
- Node.js (for job handler)
- Access to docker in Colima VM
- PostgreSQL (for storing artifacts)

---

## Success Criteria

**MVP (Minimum Viable Product)**:
- ✅ `lcm-sandbox create` works end-to-end
- ✅ Worktree created/synced correctly
- ✅ Container launches with correct permissions
- ✅ Agent can work inside (files writable as expected)
- ✅ Artifacts captured (commits, diffs, logs)
- ✅ Results returned as JSON
- ✅ Basic documentation

**Phase 2 (AIDevOps integration)**:
- ✅ Job handler spawns sandbox
- ✅ Results stored in DB
- ✅ Cleanup after merge
- ✅ Audit trail complete

---

## Timeline

- **Week 1-2**: Core CLI + Phase 0-3
- **Week 2-3**: Docker integration + Phase 4
- **Week 3**: Artifact capture + Phase 6
- **Week 4**: Docs, tests, packaging
- **Week 5** (optional): AIDevOps integration

**Total**: 4 weeks for MVP, 5 weeks with AIDevOps integration

---

## Implementation Status (Updated 2026-06-15, post-commit)

| Phase | Scope | Status |
| :---- | :---- | :----- |
| 1 | Core CLI + Phases 0–3 (preflight, worktree, sync, docker image check) | ✅ **Complete & committed** (`39e6517`) — 43 Phase-1 unit tests passing |
| 2 | Docker image + STEP 4.5 entrypoint | 🟡 **Partially implemented & committed** (`829ff5a`) — `scripts/Dockerfile.hermes`, `scripts/entrypoint.sh`, `scripts/build-hermes-image.sh` landed. Still missing: `docker-git-hooks.sh`, `rm-shim.sh`, `smoke-test.sh`, `apply_agent_profile.py`, agent-profile templates. |
| 3 | Artifact capture + cleanup command | ⬜ Not started |
| 4 | Container launch (`docker_launcher.py`) + `launch`/`stop`/`status` CLI commands | 🟡 **Implemented & committed** (`51697aa`) — launcher + CLI extensions in main. Integration test skipped until `lcm-hermes-agent:latest` image is built. Hardening flags (`--cap-drop=ALL`, `--security-opt=no-new-privileges`, `--read-only`) and `--egress-allowlist` flag still to verify. |
| 5 | AIDevOps integration + live MCP back-channel | ⬜ **Designed** — see `SANDBOX-ORCHESTRATION.md`, `SANDBOX-FLOWS.html`. Implementation blocked on verifying GH `claude-code` issues #28293 + #36665. |
| WP-8 | HERMES persona renderer + capturer (added scope) | 🟡 **Implemented & committed** (`c0b0bb7`) — `lcm_sandbox/persona/{renderer,capturer,cli}.py`; new console scripts `persona-state-renderer`, `persona-state-capturer`. 7/7 tests currently red under local Privoxy proxy interception. Source-of-truth design lives in an external `HERMES-PERSONA-INTEGRATION-PLAN` doc that is not in this repo. |
| Manual dev-sandbox template | Hand-driven sandbox (image + launcher + runbook + `/dev-sandbox` skill) | ✅ **Complete & committed** (`51c77e2`) — separate from agentic flow. Used to provide a human-driven Dockerized shell with Claude Code + Codex CLI + Gemini CLI on any host repo. See `scripts/README-dev-sandbox.md`. |
| Docs/packaging (was Phase 4 in original plan) | `USAGE.md`, `TROUBLESHOOTING.md`, `CHANGELOG.md`, pip packaging, E2E suite | ⬜ Not started. `uv.lock` is now present — document the `uv` workflow when these land. |

**Resume point:** close the remaining Phase 2 follow-ups (git hooks, rm-shim, smoke-test, agent-profile templates) and the Phase 4 hardening flags, fix the WP-8 persona-test Privoxy issue, then start Phase 3 (artifact capture).

### Pre-Phase-2 verification gates

Before Phase 5 implementation can start (and to inform the Phase 2 entrypoint's `.mcp.json` handling), verify the following against the installed Claude Code version:
- GitHub `anthropics/claude-code` **issue #28293** — custom auth headers in `.mcp.json` forwarded on every POST? Blocks the bearer-in-header pattern if still open.
- GitHub `anthropics/claude-code` **issue #36665** — server-push notifications support? Determines whether Phase 5 can rely on `elicitation/create` / `notifications/*` or must use the polling fallback (`get_more_context()` heartbeat).

### Cross-repo follow-ups

- `openrouter-agent/docs/lcm-sandbox-integration.md` currently documents the spawn-and-wait integration model. When Phase 5 lands, that doc must be updated to reflect the live MCP back-channel design (see `SANDBOX-ORCHESTRATION.md`).
- Consumer repos with `.aidevops/` directories (`go-madeira`, `arionetworks-website`, `ballaty-rentals`) should be sampled for existing `.mcp.json` content before the Phase 2 entrypoint sanitize step is finalized.

### Deferred items (still tracked)

- **Codex CLI audit + image variant** — Codex's non-interactive mode support is unverified; until then, the universal image ships Claude-only.
- **Gemini CLI audit + image variant** — same as Codex.
- **`CHANGELOG.md`** — does not exist; start using one when Phase 2 work begins.
