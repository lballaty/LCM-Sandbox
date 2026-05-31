# Agent Instructions: Autonomous Implementation of LCM-Sandbox

This document guides an autonomous agent (Claude Code, etc.) through the implementation of LCM-Sandbox phases.

## Context & Constraints

### What You're Building

A Python CLI tool (`lcm-sandbox`) that:
1. Creates isolated git worktrees on the host machine
2. Syncs them to the latest main branch
3. Launches Docker containers with restricted OS permissions
4. Allows agents to run in `bypassPermissions` mode safely
5. Captures and audits all results (commits, diffs, logs)
6. Enables multi-step plans where worktrees persist across executions

### Operating Principles

1. **Read architecture first**: Before coding any phase, read the corresponding section in SANDBOX-DETAILED-FLOW.md. It specifies exact bash commands, error handling, and verification steps.

2. **Test as you go**: Unit tests for each module. Integration tests for phases 1+2. Mock git/docker where helpful, but test with real repo/container when possible.

3. **No speculative abstractions**: Code only what each phase requires. Don't add "future-proofing" beyond the current phase.

4. **Fail loudly**: Every phase should have clear error messages with specific reasons (repo missing, branch not found, docker not running, etc.).

5. **Log everything**: Use structured logging throughout. Include phase, step, context (repo_path, branch_name, sandbox_id).

6. **Audit trail first**: Design for storage in AIDevOps DB from the start. Every significant action should be loggable.

---

## Phase 1: Core CLI + Worktree/Sync/Docker Prep (Weeks 1-2)

### Goal

User can run:
```bash
lcm-sandbox create --repo /path/to/repo --allowed-paths '{"write":["src/"]}'
```

And get back a JSON response with status and basic info.

### Deliverables

- [ ] Python package structure (`lcm_sandbox/` with all subdirs)
- [ ] Phase 0: Pre-flight validation (8 checks)
- [ ] Phase 1: Worktree creation/reset
- [ ] Phase 2: Sync to latest main
- [ ] Phase 3: Docker image check/build
- [ ] CLI entry point with error handling
- [ ] Unit tests for each core module

### Key Files to Create

```
lcm_sandbox/
├── __init__.py
├── cli.py                          # Entry point: lcm-sandbox command
├── commands/
│   ├── __init__.py
│   └── create.py                   # Main: create sandbox
├── core/
│   ├── __init__.py
│   ├── config.py                   # Load/validate config
│   ├── preflight.py                # Phase 0: 8 checks
│   ├── worktree.py                 # Phase 1: create/reset worktree
│   ├── sync.py                     # Phase 2: fetch/rebase/verify
│   ├── docker_builder.py           # Phase 3: check/build image
│   └── artifact_capture.py         # Phase 6 stub for now
├── models/
│   ├── __init__.py
│   ├── sandbox_config.py           # Pydantic models
│   └── artifact.py                 # Result models
├── utils/
│   ├── __init__.py
│   ├── git.py                      # Git command wrappers
│   ├── docker.py                   # Docker command wrappers
│   ├── shell.py                    # Subprocess helpers
│   └── logger.py                   # Structured logging
└── tests/
    ├── __init__.py
    ├── test_preflight.py
    ├── test_worktree.py
    ├── test_sync.py
    ├── test_docker_builder.py
    └── conftest.py                 # Pytest fixtures

setup.py                            # Package metadata + entry point
requirements.txt                    # Dev dependencies
pyproject.toml                      # Modern Python config
pytest.ini                          # Test configuration
```

### Implementation Order

1. **Setup** (`setup.py`, `requirements.txt`, `pyproject.toml`)
   - Dependencies: pydantic, click, docker, gitpython, requests, pyyaml
   - Entry point: `lcm-sandbox=lcm_sandbox.cli:main`

2. **Models** (`models/sandbox_config.py`)
   ```python
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
   ```

3. **Utils** (`utils/git.py`, `utils/docker.py`, `utils/shell.py`, `utils/logger.py`)
   - Wrap subprocess calls for clarity
   - Example: `git.fetch_origin(repo_path)` instead of raw subprocess

4. **Phase 0: Preflight** (`core/preflight.py`)
   - Read SANDBOX-DETAILED-FLOW.md STEP 0.1 for exact checks
   - 8 checks: repo, branch, colima, docker, json, structure, timeout, no-duplicate-sandbox
   - Raise `PrefailCheckError` if any check fails

5. **Phase 1: Worktree** (`core/worktree.py`)
   - Read SANDBOX-DETAILED-FLOW.md STEP 1.1 and 1.2
   - STEP 1.1: Create new worktree (verify origin/main, ensure branch, create with `git worktree add`)
   - STEP 1.2: Reset existing worktree (if reusing from multi-step plan)
   - Verify worktree created with `.git` file present

6. **Phase 2: Sync** (`core/sync.py`)
   - Read SANDBOX-DETAILED-FLOW.md STEP 2.1-2.6
   - STEP 2.1: Fetch origin main
   - STEP 2.2: Check status (in_sync/behind/ahead/diverged)
   - STEP 2.3: Handle each status (rebase if behind)
   - STEP 2.4-2.6: Verify clean, correct branch, log baseline
   - Log worktree baseline state (latest commit, timestamp)

7. **Phase 3: Docker Prep** (`core/docker_builder.py`)
   - Read SANDBOX-DETAILED-FLOW.md STEP 3.1-3.2
   - STEP 3.1: Check if `lcm-dev-agent:latest` exists, build if not
   - STEP 3.2: Verify image is functional (test git/claude/codex versions)
   - Return image ID

8. **CLI** (`cli.py`)
   - Use click or argparse (click recommended for better UX)
   - Main command: `create`
   - Args: `--repo`, `--branch`, `--allowed-paths`, `--timeout`, `--colima-profile`
   - Orchestrate phases 0-3 in order
   - Return JSON with status, worktree_path, etc.

9. **Tests** (`tests/test_*.py`)
   - Unit: Mock git/docker, test each module in isolation
   - Integration: Use real test repo, real docker (optional for Phase 1)
   - Run: `pytest lcm_sandbox/tests/`

### Success Criteria

```bash
$ lcm-sandbox create \
  --repo /tmp/test-repo \
  --branch test/sandbox \
  --allowed-paths '{"write":["src/"],"read":["*"]}'

# Output (JSON):
{
  "sandbox_id": "sandbox-run_xxx-20260531T120000Z",
  "status": "ready_for_docker_launch",
  "worktree_path": "/tmp/test-repo/.sandbox-worktrees/sandbox-run_xxx-...",
  "repo_path": "/tmp/test-repo",
  "branch": "test/sandbox",
  "latest_commit": "abc123...",
  "phase": 3,
  "next_step": "docker_launch"
}

# Exit code: 0
```

### Notes

- **No container launch yet** — Phase 1 stops after Docker image prep. Phase 2 adds container launch.
- **Worktree reuse** — Check if worktree exists from previous step. If yes, reset it. If no, create new.
- **Error handling** — Every step should raise a specific error with context (repo_path, branch_name, step number).
- **Logging** — Use structured logging so output can be parsed by AIDevOps.

---

## Phase 2: Docker Integration + Container Launch (Weeks 2-3)

### Goal

Container launches, entrypoint runs, agent can execute inside, phase 4.5 sets up permissions.

### Deliverables

- [ ] Finalized Dockerfile with ALLOWED_PATHS arg
- [ ] docker-entrypoint.sh (Phase 4.5 steps 4.5.1-4.5.10)
- [ ] docker-git-hooks.sh (pre-push hook)
- [ ] Phase 4: Container launch (`core/docker_launcher.py`)
- [ ] Integration tests (launch container, verify permissions)

### Key Files to Create

```
scripts/
├── docker-entrypoint.sh            # Container startup (bash)
└── docker-git-hooks.sh             # Pre-push hook generator (bash)

lcm_sandbox/
├── core/
│   └── docker_launcher.py          # Phase 4: launch + monitor
├── commands/
│   └── create.py                   # Updated to call docker_launcher
```

### Implementation Order

1. **Dockerfile**
   - Read SANDBOX-DETAILED-FLOW.md (Dockerfile section)
   - `FROM ubuntu:24.04`
   - Install: git, nodejs, npm, claude-code, codex
   - Create user: `aiagent` (non-sudo)
   - Copy scripts and set entrypoint

2. **docker-entrypoint.sh** (Phase 4.5)
   - Steps 4.5.1-4.5.10 in SANDBOX-DETAILED-FLOW.md
   - Parse and validate env vars
   - Lock down all files to 644
   - Apply allowed_paths (chmod 755 + chown aiagent)
   - Install pre-push hook
   - Write sandbox manifest
   - Switch to aiagent user
   - Verify git state
   - Drop to bash -i

3. **docker-git-hooks.sh**
   - Generate pre-push hook that exits 1 (deny all pushes)
   - Called from entrypoint

4. **Phase 4: docker_launcher.py**
   - Read SANDBOX-DETAILED-FLOW.md STEP 4.1-4.5
   - STEP 4.1: Prepare env vars
   - STEP 4.2: Verify volume mounts exist
   - STEP 4.3: Launch container with docker run + all mounts/labels/env
   - STEP 4.4: Verify container is running (docker ps)
   - Monitor: Wait for exit, capture logs
   - Return exit code

5. **Updated cli.py / create.py**
   - After Phase 3 completes, call docker_launcher
   - Capture output (stdout/stderr from container)
   - Return extended JSON with command_result

### Integration Tests

```python
def test_container_launches():
    # Create real test repo, run lcm-sandbox create
    # Verify container appears in docker ps
    # Verify entrypoint ran (files locked down)

def test_file_acls():
    # Launch container with allowed_paths={"write":["src/"]}
    # Try to touch /workspace/src/test.txt (should succeed)
    # Try to touch /workspace/docs/test.txt (should fail)

def test_git_constraints():
    # Launch container
    # Create a commit (should succeed)
    # Try push to origin (should fail via pre-push hook)
```

### Success Criteria

```bash
$ lcm-sandbox create ... --command "echo hello && git status"

# Output (extended JSON):
{
  "sandbox_id": "sandbox-run_xxx-...",
  "status": "completed",
  "command_result": {
    "stdout": "hello\nOn branch test/sandbox...",
    "stderr": "",
    "exit_code": 0
  },
  "num_commits": 0,  # No commits made
  "files_modified": 0
}
```

---

## Phase 3: Artifact Capture + Cleanup (Week 3)

### Goal

Results captured (commits, diffs, logs), stored locally or in DB, worktree cleanup script created.

### Deliverables

- [ ] Phase 6: Artifact capture (`core/artifact_capture.py`)
- [ ] Cleanup command (`commands/cleanup.py`)
- [ ] S3 upload or filesystem archival
- [ ] DB integration stubs (or local JSON for MVP)

### Key Implementation

1. **Phase 6: artifact_capture.py**
   - Read SANDBOX-DETAILED-FLOW.md STEP 6.1-6.10
   - Capture: exit code, logs, git state, stats, violations
   - Calculate: num_commits, files_modified, lines added/deleted
   - Validate: changes within allowed_paths
   - Archive: to /archive/sandbox/{sandbox_id}/ or S3
   - Store: in DB (or JSON file for MVP)
   - Return JSON with all metadata

2. **commands/cleanup.py**
   - Input: sandbox_id
   - Steps:
     - Verify merge to main succeeded (git log check)
     - Archive final artifacts
     - Delete worktree: `git worktree remove {worktree_path}`
     - Update DB status
     - Log cleanup event

### Success Criteria

```bash
$ lcm-sandbox create ... --command "touch src/test.txt && git add . && git commit -m 'test'"

# Output (extended JSON with artifacts):
{
  "sandbox_id": "sandbox-run_xxx-...",
  "status": "completed",
  "num_commits": 1,
  "files_modified": 1,
  "lines_added": 0,
  "lines_deleted": 0,
  "violations": [],
  "artifacts_url": "s3://... or file://...",
  "worktree_path": "/path/to/.sandbox-worktrees/..."
}

$ lcm-sandbox cleanup --sandbox-id sandbox-run_xxx-...
# Output:
{
  "sandbox_id": "sandbox-run_xxx-...",
  "status": "cleaned_up_successfully",
  "worktree_deleted": true,
  "freed_disk_bytes": 245000000
}
```

---

## Phase 4: Documentation + Testing (Week 4)

### Deliverables

- [ ] USAGE.md (CLI examples)
- [ ] API.md (Python module usage)
- [ ] TROUBLESHOOTING.md (Common issues)
- [ ] E2E test suite
- [ ] Package for distribution (setup.py sdist bdist_wheel)

### E2E Test Example

```python
def test_e2e_full_workflow():
    # 1. Create test repo
    test_repo = create_temp_repo()

    # 2. Run lcm-sandbox create
    result = run_sandbox(
        repo=test_repo,
        branch="test/e2e",
        allowed_paths={"write": ["src/"], "read": ["*"]},
        command="python3 -c 'open(\"src/test.py\", \"w\").write(\"print(1)\\n\")' && git add . && git commit -m 'test'"
    )

    # 3. Verify results
    assert result['status'] == 'completed'
    assert result['exit_code'] == 0
    assert result['num_commits'] == 1
    assert result['files_modified'] == 1
    assert result['violations'] == []

    # 4. Cleanup
    cleanup_result = run_cleanup(result['sandbox_id'])
    assert cleanup_result['worktree_deleted'] == True
```

---

## Phase 5: AIDevOps Integration (Week 5, Optional)

### Deliverables

- [ ] Job handler in AIDevOps (`JOB_HANDLERS['platform:sandbox-run']`)
- [ ] Approval gate integration
- [ ] Webhook cleanup on merge
- [ ] DB storage + audit logging

### Implementation

See `/Users/liborballaty/LocalProjects/GitHubProjectsDocuments/openrouter-agent/docs/lcm-sandbox-integration.md` for AIDevOps integration details.

---

## Testing & Validation Strategy

### Manual Testing (Throughout All Phases)

```bash
# Phase 1 test
lcm-sandbox create --repo /tmp/test-repo --branch test/sandbox --allowed-paths '{"write":["src/"]}'

# Phase 2 test
lcm-sandbox create ... --command "git status && ls -la"

# Phase 3 test
lcm-sandbox create ... --command "touch src/test.txt && git add . && git commit -m 'test'"
lcm-sandbox cleanup --sandbox-id sandbox-run_xxx-...

# Phase 4: Full E2E
pytest lcm_sandbox/tests/ -v
```

### Unit Tests

- Mock git operations: use `unittest.mock.patch`
- Mock docker operations: use fake docker SDK responses
- Test error paths: missing repo, invalid branch, docker not running, etc.

### Integration Tests

- Use real git repo (create temp test repo)
- Use real docker (if colima available)
- Clean up after tests (remove worktrees, containers)

---

## Error Handling Guidelines

Every phase should emit clear, specific errors:

```python
# BAD:
raise Exception("Error in phase 1")

# GOOD:
raise WorktreeError(
    f"Failed to create worktree at {worktree_path}: "
    f"Branch {branch_name} does not exist locally or on origin"
)
```

Include context in error messages:
- What was being attempted (phase, step)
- What failed (repo_path, branch_name, etc.)
- Why it failed (specific subprocess output)
- How to fix it (suggested action)

---

## Logging Guidelines

Use structured logging throughout:

```python
import logging
logger = logging.getLogger(__name__)

logger.info(
    "phase_complete",
    extra={
        "phase": 1,
        "worktree_path": worktree_path,
        "branch": branch_name,
        "latest_commit": latest_commit,
        "timestamp": datetime.now().isoformat()
    }
)
```

This enables AIDevOps to parse and audit every action.

---

## When Stuck

1. **Re-read the detailed flow** — SANDBOX-DETAILED-FLOW.md has every bash command and expected output
2. **Check the examples** — Look at existing similar tools (e.g., how does gitpython handle worktrees?)
3. **Test incrementally** — Get one phase working before moving to the next
4. **Ask for clarification** — Flag ambiguities in the spec to the user

---

## Success Definition

At the end of each phase:

- ✅ All deliverables complete
- ✅ Unit tests pass
- ✅ Code is readable and well-structured
- ✅ Error messages are clear and actionable
- ✅ Logging is structured and auditable
- ✅ Manual test workflow succeeds end-to-end

Good luck! 🚀
