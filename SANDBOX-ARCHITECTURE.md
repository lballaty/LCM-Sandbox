# Sandbox Architecture Diagram

---

## 📋 REFERENCE: Detailed Granular Flow

For **step-by-step implementation details** with exact bash commands, expected outputs, and error handling:

→ **[SANDBOX-DETAILED-FLOW.md](./SANDBOX-DETAILED-FLOW.md)**

Contains:
- **Phase 0**: Pre-flight checks (8 validation steps)
- **Phase 1**: Worktree prep (create new or reset existing)
- **Phase 2**: Sync to latest main (critical sync step with error handling)
- **Phase 3**: Docker image prep
- **Phase 4**: Container launch (env, mounts, entrypoint steps)
- **Phase 5**: Agent execution
- **Phase 6**: Artifact capture & cleanup

This document (SANDBOX-ARCHITECTURE.md) shows the big picture and flows. The detailed flow document shows every bash command and verification step.

---

## 1. Local Developer Workflow

```
┌─────────────────────────────────────────────────────────────────────┐
│ Developer Machine (macOS)                                           │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  $ lcm-sandbox create \                                             │
│    --repo /path/to/openrouter-agent \                              │
│    --allowed-paths '{"write":["src/"],"read":["*"]}' \             │
│    --command "claude --mode bypassPermissions"                      │
│                                                                     │
│                           ↓                                          │
│                  Python CLI Tool                                    │
│        (globally installed: /usr/local/bin/lcm-sandbox)            │
│                           │                                         │
│      ┌────────────────────┼────────────────────┐                   │
│      │                    │                    │                   │
│      ↓                    ↓                    ↓                   │
│  Git Ops           Docker Ops            Config Setup              │
│  --------           ---------             -----------              │
│  • Create or        • Check image        • Parse allowed           │
│    use existing       exists              paths (JSON)            │
│    worktree         • Build if           • Set env vars           │
│  • Checkout           needed              • Output JSON            │
│    branch           • Mount paths                                   │
│                     • Run container                                │
│                                                                     │
│                           ↓                                          │
│        Colima VM (LCM-Dev profile)                                 │
│  ┌────────────────────────────────────────────────────────────┐   │
│  │ Docker Container: lcm-dev-agent                           │   │
│  ├────────────────────────────────────────────────────────────┤   │
│  │ FROM ubuntu:24.04                                         │   │
│  │ USER aiagent (non-sudo)                                   │   │
│  │                                                            │   │
│  │ /workspace (mounted worktree)                             │   │
│  │  └─ src/          → 755 (writable by aiagent)            │   │
│  │  └─ docs/         → 644 (read-only)                      │   │
│  │  └─ package.json  → 644 (read-only)                      │   │
│  │  └─ .git          → shared reference to parent repo      │   │
│  │                                                            │   │
│  │ Pre-push hook (git): deny origin pushes                   │   │
│  │ Installed tools: Claude Code, ChatGPT Codex              │   │
│  │ Environment: bypassPermissions mode (safe = bounded user) │   │
│  └────────────────────────────────────────────────────────────┘   │
│                           ↓                                          │
│    Output (JSON to stdout or file)                                │
│    ────────────────────────────────────────────────────────────    │
│    {                                                              │
│      "container_id": "abc123...",                                │
│      "workspace": "/workspace",                                  │
│      "git_branch": "test/agent-sandbox",                         │
│      "ready": true,                                              │
│      "command_result": {                                         │
│        "stdout": "...",                                          │
│        "stderr": "",                                             │
│        "exit_code": 0                                            │
│      }                                                            │
│    }                                                              │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. AIDevOps Workflow Integration

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ AIDevOps Platform (Node.js Server on Port 9700)                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────┐           │
│  │ API: POST /api/workflows/:id/run                           │           │
│  │ Payload: { agent_id, intent, risk_level: 'high' }          │           │
│  └─────────────────────────────────────────────────────────────┘           │
│                           ↓                                                 │
│  ┌─────────────────────────────────────────────────────────────┐           │
│  │ DBOS Workflow: executeScheduledJob()                        │           │
│  │ (Durable task orchestration with crash recovery)           │           │
│  └─────────────────────────────────────────────────────────────┘           │
│                           ↓                                                 │
│  ┌─────────────────────────────────────────────────────────────┐           │
│  │ Risk Classification & Approval Gate                         │           │
│  │ - If risk_level = 'high' → emit approval request           │           │
│  │ - Wait for human approval (1-hour timeout)                 │           │
│  │ - Log to agent_run_approvals table                         │           │
│  └─────────────────────────────────────────────────────────────┘           │
│                           ↓                                                 │
│              ┌────────────────────────────┐                                 │
│              │ Approval Granted?          │                                │
│              └────────────────────────────┘                                │
│                    YES ↓                NO ↓                               │
│                        │                   Fail run, exit                  │
│                        │                                                   │
│  ┌─────────────────────────────────────────────────────────────┐           │
│  │ Activity: runJob()                                          │           │
│  │ (Tier 2: Subprocess execution)                             │           │
│  └─────────────────────────────────────────────────────────────┘           │
│                           ↓                                                 │
│              ┌────────────────────────────────────────┐                     │
│              │ Dispatch to Job Handler               │                     │
│              │ (JOB_HANDLERS registry)               │                     │
│              └────────────────────────────────────────┘                     │
│               ↓                         ↓           ↓                       │
│  ┌──────────────────────┐  ┌──────────────────┐  ┌──────────────────┐     │
│  │ Browser Automation   │  │ Sandbox Run      │  │ Other Handlers   │     │
│  │ (Python subprocess)  │  │ (NEW - Python)   │  │ (Custom jobs)    │     │
│  └──────────────────────┘  └──────────────────┘  └──────────────────┘     │
│                               ↓                                             │
│                    ┌──────────────────────────────┐                         │
│                    │ child_process.spawn()        │                        │
│                    │ 'lcm-sandbox create' ...     │                        │
│                    └──────────────────────────────┘                         │
│                               ↓                                             │
│          ┌────────────────────────────────────────────┐                    │
│          │ Configuration passed via:                 │                    │
│          │  - CLI args: --repo, --allowed-paths      │                    │
│          │  - Env vars: AIDEVOPS_REPO_ID, etc        │                    │
│          │  - Timeout: --timeout <minutes>           │                    │
│          │  - Command: --command "<agent task>"      │                    │
│          └────────────────────────────────────────────┘                    │
│                               ↓                                             │
│          ╔════════════════════════════════════════════╗                    │
│          ║     Docker Container (as above)           ║                    │
│          ║     Running agent in isolated env         ║                    │
│          ╚════════════════════════════════════════════╝                    │
│                               ↓                                             │
│          ┌────────────────────────────────────────────┐                    │
│          │ Capture stdout/stderr → JSON              │                    │
│          │ Store git diff (commits only, no push)   │                    │
│          │ Create artifact record                    │                    │
│          └────────────────────────────────────────────┘                    │
│                               ↓                                             │
│  ┌─────────────────────────────────────────────────────────────┐           │
│  │ Store in Database:                                          │           │
│  │ - workflow_runs: status=completed                           │           │
│  │ - agent_artifacts: command_output, git_diff                │           │
│  │ - agent_tool_calls: tool=sandbox, input/output              │           │
│  │ - audit_events: action=sandbox_run, result                  │           │
│  └─────────────────────────────────────────────────────────────┘           │
│                           ↓                                                 │
│  ┌─────────────────────────────────────────────────────────────┐           │
│  │ Resume DBOS Workflow with results                           │           │
│  │ - Continue next step or finalize                            │           │
│  │ - Return to caller with status: completed                   │           │
│  └─────────────────────────────────────────────────────────────┘           │
│                           ↓                                                 │
│  ┌─────────────────────────────────────────────────────────────┐           │
│  │ API Response: 200 OK                                        │           │
│  │ {                                                           │           │
│  │   "status": "completed",                                    │           │
│  │   "artifacts": ["command_output", "git_diff"],              │           │
│  │   "run_id": "run_abc123...",                                │           │
│  │   "audit_event_id": "event_xyz789..."                       │           │
│  │ }                                                            │           │
│  └─────────────────────────────────────────────────────────────┘           │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Sandbox Container Internals

```
┌──────────────────────────────────────────────────────────────────┐
│ Docker Container: lcm-dev-agent                                  │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Image: lcm-dev-agent:latest                                    │
│  Base: ubuntu:24.04                                             │
│  User: aiagent (UID 1000, non-sudo)                             │
│  Group: agentgroup (GID 1000, no sudo capabilities)             │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ ENTRYPOINT: /entrypoint.sh                              │   │
│  │                                                          │   │
│  │ 1. Read ALLOWED_PATHS from env var (JSON)              │   │
│  │    e.g., {"write": ["src/", "docs/"], "read": ["*"]}  │   │
│  │                                                          │   │
│  │ 2. chmod 644 /workspace/*  # Make all read-only        │   │
│  │                                                          │   │
│  │ 3. For each path in ALLOWED_PATHS.write:                │   │
│  │    chmod 755 /workspace/<path>                         │   │
│  │    chown aiagent:agentgroup /workspace/<path>          │   │
│  │                                                          │   │
│  │ 4. Install git pre-push hook                           │   │
│  │    echo '#!/bin/bash'                                  │   │
│  │    echo 'exit 1'  # Deny all pushes to origin          │   │
│  │    > .git/hooks/pre-push                               │   │
│  │    chmod +x .git/hooks/pre-push                        │   │
│  │                                                          │   │
│  │ 5. git config --global safe.directory /workspace       │   │
│  │    (Trust mounted directory)                           │   │
│  │                                                          │   │
│  │ 6. Execute main command or drop to bash                │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ Mounted Volumes (from host)                             │   │
│  │                                                          │   │
│  │ /workspace (read-write)                                │   │
│  │  └─ Worktree checkout: /host/repo-sandbox/           │   │
│  │  └─ Contains: .git (file), src/, docs/, etc.         │   │
│  │                                                          │   │
│  │ /workspace/.git → parent repo's .git directory        │   │
│  │  └─ Shared git metadata + objects                      │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ Installed Tools                                         │   │
│  │                                                          │   │
│  │ node --version                                         │   │
│  │ npm --version                                          │   │
│  │ git --version                                          │   │
│  │ claude --version (Claude Code CLI)                     │   │
│  │ codex --version (ChatGPT Codex)                        │   │
│  │ python3 (standard Ubuntu python)                       │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ Execution Environment                                   │   │
│  │                                                          │   │
│  │ Claude Code runs in: bypassPermissions mode            │   │
│  │  → No internal safety checks                           │   │
│  │  → OS-level user (aiagent) enforces boundary          │   │
│  │  → File ACLs restrict modifications                    │   │
│  │  → Git hooks block origin pushes                       │   │
│  │                                                          │   │
│  │ Example agent execution:                              │   │
│  │  $ claude --permission-mode bypassPermissions         │   │
│  │  > edit src/file.js   ← OK (allowed path, writable)   │   │
│  │  > rm docs/README.md  ← FAIL (not writable)           │   │
│  │  > git commit ...     ← OK                             │   │
│  │  > git push origin    ← FAIL (pre-push hook blocks)   │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

---

## 4. Data Flow: AIDevOps → Sandbox → Artifact

```
API Request
    ↓
AIDevOps Server
    ↓ (DBOS step)
[Risk Classification]
    ↓
    ├─→ If low/medium: execute inline
    └─→ If high/critical: approval gate
         ↓
    [Wait for approval]
         ↓
[Subprocess spawn: lcm-sandbox create ...]
    ↓
    ├─ mount /repo-sandbox:/workspace
    ├─ mount /repo/.git:/repo/.git
    ├─ set ALLOWED_PATHS={"write":["src/"]}
    ├─ run container as aiagent user
    └─ timeout 30 minutes
    ↓
[Container startup]
    ├─ /entrypoint.sh locks down permissions
    ├─ Sets src/ writable, rest read-only
    ├─ Installs git pre-push hook
    └─ Starts bash or executes command
    ↓
[Agent executes in container]
    ├─ Claude Code in bypassPermissions
    ├─ Agent modifies src/file.js
    ├─ Agent commits changes
    └─ Agent tries push → blocked by hook
    ↓
[Capture output]
    ├─ stdout → artifact_output.txt
    ├─ stderr → artifact_errors.txt
    ├─ git diff → artifact_diff.patch
    └─ exit code → status
    ↓
[Return to AIDevOps]
    ├─ subprocess returns JSON
    ├─ stored in agent_artifacts table
    ├─ workflow continues
    └─ audit event logged
    ↓
API Response: 200 OK with results
```

---

## 5. Threat Model & Containment

```
┌─────────────────────────────────────────────────────────────┐
│ Attack/Failure Scenario                  │  Contained By    │
├──────────────────────────────────────────┼──────────────────┤
│ Agent deletes arbitrary files            │ File ACLs        │
│                                          │ (locked down)    │
│ Agent runs rm -rf /                      │ Non-sudo user    │
│ (attempt to destroy filesystem)          │ (can't escalate) │
│                                          │                  │
│ Agent pushes to origin/main               │ Git pre-push hook│
│ (tries to push unsafe code)              │ (blocks origin)  │
│                                          │                  │
│ Agent modifies outside /workspace         │ Container wall   │
│ (tries to escape sandbox)                │ (no host access) │
│                                          │                  │
│ Agent calls git clone evil/repo          │ File ACL         │
│ (only src/ write)                        │                  │
│                                          │                  │
│ Agent creates fork bomb / DoS            │ Container limits │
│ (CPU, memory, timeout)                   │                  │
│                                          │                  │
│ Compromised LLM provider (injection)     │ Audit trail      │
│ (recorded, but not prevented)            │                  │
└──────────────────────────────────────────┴──────────────────┘

Note: This is NOT a replacement for code review. It prevents
unintended side effects (accidental overwrites) and enforces
process discipline (no direct main pushes). A malicious agent
or injected prompt can still attempt destructive acts, but
within bounded scope.
```

---

## 6. Prerequisite: Worktree Creation Flow

```
CRITICAL SEQUENCE (happens on HOST MACHINE):

1. Approval Granted (or low-risk task proceeds)
   ↓
2. Generate sandbox_id = "sandbox-{run_id}-{timestamp}"
   ↓
3. WORKTREE CREATION ← THIS IS A REQUIRED STEP, NOT OPTIONAL
   │
   ├─ Decide worktree location:
   │  {repo_parent}/.sandbox-worktrees/sandbox-{run_id}/
   │  Example: /Users/libor/.../openrouter-agent/.sandbox-worktrees/sandbox-run_def456uvw/
   │
   ├─ Check if worktree exists:
   │  if [ -d "{worktree_path}/.git" ]
   │
   ├─ If EXISTS (step 2+ of multi-step plan):
   │  └─ Clean and reset to branch:
   │     git checkout {branch_name}
   │     git reset --hard origin/{branch_name}
   │     git clean -fd
   │
   ├─ If NOT EXISTS (step 1 or first time):
   │  └─ Create fresh worktree:
   │     git worktree add \
   │       {worktree_path} \
   │       -b {branch_name} \
   │       origin/main
   │
   └─ Verify:
      ls -la {worktree_path}/.git  ← Should exist
      cd {worktree_path} && git branch  ← Should show {branch_name}
   ↓
4. SYNC WORKTREE TO LATEST MAIN ← CRITICAL BEFORE SANDBOX EXECUTION
   │
   ├─ Fetch latest from origin:
   │  cd {worktree_path}
   │  git fetch origin
   │
   ├─ Check if in sync:
   │  git log -1 --oneline
   │  git log -1 --oneline origin/main
   │  ← Compare to see if behind/ahead
   │
   ├─ If behind main:
   │  └─ Rebase to incorporate latest main:
   │     git rebase origin/main
   │     ← Replays any local commits on top of latest main
   │
   ├─ If ahead (should not happen, alert if it does):
   │  └─ Investigate unpushed commits
   │
   └─ Verify clean working directory:
      git status  ← Should show "nothing to commit, working tree clean"
   ↓
5. NOW we have a valid, IN-SYNC worktree on HOST MACHINE
   ↓
5. Mount synced worktree into Docker container:
   docker run -v {worktree_path}:/workspace ...
   ↓
6. Container starts with /workspace = worktree in sync with latest main
   ↓
   - All commits from previous steps (if any) are in git history
   - All commits from main are in git history
   - Working directory is CLEAN (no unstaged changes)
   - Ready for agent to make new changes
   ↓
7. Agent executes inside container (with latest baseline)
```

**KEY POINT**: The worktree MUST be created on the HOST before `docker run`. The Docker `-v` flag just mounts an existing directory. If worktree doesn't exist, the mount will fail or give empty directory.

---

## 6. Detailed Flow: Plan → Sandbox Parameters → Execution → Results

[See SANDBOX-DETAILED-FLOW.md for the comprehensive detailed flow including planning, parameter extraction, approval, sandbox creation, and multi-step plan management]

---

## 7. Integration Checklist

### Local Developer

- [ ] Install lcm-sandbox globally: `pip install lcm-sandbox`
- [ ] Create worktree: `git worktree add ../repo-sandbox test-branch`
- [ ] Run sandbox: `lcm-sandbox create --repo ../repo-sandbox --allowed-paths '{"write":["src/"]}'`
- [ ] Drop into shell or run command
- [ ] Review changes: `git diff --staged`
- [ ] Cleanup: `lcm-sandbox cleanup --container-id <id>`

### AIDevOps Integration

- [ ] Job handler: `JOB_HANDLERS['platform:sandbox-run']` → subprocess dispatch
- [ ] Or workflow step: `step.type: 'sandbox'` → action-dispatcher
- [ ] Or Python module: `from lcm_sandbox import SandboxRunner`
- [ ] Approval gating: high/critical runs wait for approval before spawning
- [ ] Artifact storage: results → agent_artifacts table
- [ ] Audit logging: every sandbox run → audit_events table
