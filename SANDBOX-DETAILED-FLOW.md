# Sandbox Execution: Detailed Granular Flow

This document specifies every step in the sandbox lifecycle with error handling and verification checks.

---

## PHASE 0: PRE-FLIGHT CHECKS (Host Machine, before anything else)

### STEP 0.1: Verify sandbox configuration

**Input**: sandbox_config = {
  plan_id, run_id, repo_path, branch_name,
  allowed_paths, timeout_minutes, colima_profile
}

**Checks**:
```bash
# Check 1: Repo exists and is git repo
cd {repo_path} && git rev-parse --git-dir
→ Must return path (e.g., ".git" or ".git/worktrees/...")

# Check 2: Branch name is valid
git show-ref refs/heads/{branch_name} || git show-ref refs/remotes/origin/{branch_name}
→ Must find the branch locally or remotely

# Check 3: Colima profile exists and running
colima -p {colima_profile} status
→ Expected output contains "Running"

# Check 4: Docker accessible
docker ps
→ Must succeed (docker daemon running)

# Check 5: allowed_paths is valid JSON
echo '{allowed_paths_json}' | jq empty
→ Must parse without error

# Check 6: Structure is correct
echo '{allowed_paths_json}' | jq 'keys | contains(["write","read"])'
→ Must have "write" and "read" keys

# Check 7: Timeout is reasonable
test {timeout_minutes} -ge 15 -a {timeout_minutes} -le 480
→ Must be between 15 and 480 minutes

# Check 8: No other sandbox with same run_id running
docker ps --filter "label=run_id={run_id}" | grep -q .
→ Must be empty (no existing container)
```

**Action**: Abort with detailed reason if ANY check fails

---

### STEP 0.2: Determine worktree path

```bash
sandbox_id = "sandbox-${run_id}-$(date -u +%Y%m%dT%H%M%SZ)"
worktree_parent = "${repo_path}/.sandbox-worktrees"
worktree_path = "${worktree_parent}/${sandbox_id}"

# Ensure parent directory exists
mkdir -p "${worktree_parent}"
```

**Example**:
- sandbox_id: `sandbox-run_def456uvw-20260530T140523Z`
- worktree_path: `/Users/libor/.../openrouter-agent/.sandbox-worktrees/sandbox-run_def456uvw-20260530T140523Z`

---

### STEP 0.3: Check if worktree already exists (multi-step plan)

**Query AIDevOps DB**:
```sql
SELECT worktree_path, sandbox_id
FROM agent_artifacts
WHERE run_id = {run_id}
AND artifact_type = 'sandbox_execution'
ORDER BY created_at DESC
LIMIT 1;
```

**Decision**:
- If found: Reuse worktree → Jump to STEP 1.2 (reset existing)
- If not found: Create new → Proceed to STEP 1.1 (create new)

---

## PHASE 1: WORKTREE PREPARATION (Host Machine)

### STEP 1.1: CREATE NEW WORKTREE (if doesn't exist)

#### 1.1.1: Verify origin/main exists

```bash
cd {repo_path}
git ls-remote origin main
```

Expected output: `<40-char-hash>  refs/heads/main`

**Action**: Abort if fails — cannot create worktree without origin/main

---

#### 1.1.2: Ensure local tracking of branch exists or create

```bash
# Check if local branch exists
git show-ref refs/heads/{branch_name}
LOCAL_EXISTS=$?

if [ $LOCAL_EXISTS -ne 0 ]; then
  # Local branch doesn't exist, check remote
  git ls-remote origin {branch_name} | grep -q refs/heads/{branch_name}
  REMOTE_EXISTS=$?

  if [ $REMOTE_EXISTS -eq 0 ]; then
    # Remote exists, create local tracking
    git branch --track {branch_name} origin/{branch_name}
  else
    # Neither local nor remote exist, create from origin/main
    git checkout -b {branch_name} origin/main
  fi
fi
```

**Expected**: Local branch now exists

---

#### 1.1.3: Create worktree with tracking

```bash
git worktree add \
  {worktree_path} \
  --track \
  {branch_name}
```

**Verify creation**:
```bash
test -d {worktree_path}/.git && echo "OK" || exit 1
cd {worktree_path} && git rev-parse HEAD
```

Expected: Commit hash is printed

---

### STEP 1.2: RESET EXISTING WORKTREE (if reusing from previous step)

#### 1.2.1: Verify worktree is still valid

```bash
cd {worktree_path}
git rev-parse --git-dir
```

Expected: Path to parent's worktree metadata (e.g., `/path/to/.git/worktrees/sandbox-xxx`)

**Action**: Abort if fails — worktree corrupted

---

#### 1.2.2: Switch to correct branch

```bash
git checkout {branch_name}
```

Expected: `Switched to branch '{branch_name}'` or `Already on '{branch_name}'`

**Action**: Abort if wrong branch

---

#### 1.2.3: Clean all changes and untracked files

```bash
git reset --hard HEAD
git clean -fd
```

**Verify**:
```bash
git status --porcelain
```

Expected: Empty output (no changes)

**Action**: Abort if any files remain

---

#### 1.2.4: Update branch tracking

```bash
git branch -u origin/{branch_name}
```

---

## PHASE 2: SYNC WORKTREE TO LATEST MAIN (Host Machine)

### STEP 2.1: Fetch latest from origin

```bash
cd {repo_path}  # IMPORTANT: parent repo, not worktree
git fetch origin main
```

**Verify**:
```bash
git rev-parse origin/main
```

Expected: 40-character commit hash (current)

**Action**: Abort if fails

---

### STEP 2.2: Check worktree sync status

In worktree:
```bash
cd {worktree_path}

# Get current state
WORKTREE_COMMIT=$(git rev-parse HEAD)
ORIGIN_MAIN=$(git rev-parse origin/main)
MERGE_BASE=$(git merge-base {branch_name} origin/main)

# Determine status
if [ "$WORKTREE_COMMIT" == "$ORIGIN_MAIN" ]; then
  STATUS="in_sync"
elif [ "$MERGE_BASE" == "$WORKTREE_COMMIT" ]; then
  STATUS="behind"
elif [ "$MERGE_BASE" == "$ORIGIN_MAIN" ]; then
  STATUS="ahead"
else
  STATUS="diverged"
fi
```

---

### STEP 2.3: Handle each status

**If STATUS = "in_sync"**:
- ✓ Worktree is at latest main
- Proceed to STEP 2.4

**If STATUS = "behind"**:
- ⚠ Worktree has previous commits, need to bring in new main
```bash
git rebase origin/main
```
- If rebase succeeds → Proceed to STEP 2.4
- If rebase conflicts → Abort with detailed error; user must manually resolve

**If STATUS = "ahead"**:
- ✗ Worktree has unpushed commits (should NOT happen)
- Log both commit histories
- Abort: "Worktree is ahead of origin/main"

**If STATUS = "diverged"**:
- ✗ Branches have diverged
- Log both commit histories
- Abort: "Cannot sync diverged branches"

---

### STEP 2.4: Verify clean working directory post-sync

```bash
cd {worktree_path}
git status --porcelain
```

Expected: Empty (no uncommitted changes)

**Action**: Abort if changes exist

---

### STEP 2.5: Verify branch is correct

```bash
git rev-parse --abbrev-ref HEAD
```

Expected: `{branch_name}`

---

### STEP 2.6: Log worktree state before Docker launch

```bash
# Store for later verification
LATEST_COMMIT=$(git rev-parse HEAD)
LATEST_COMMIT_MSG=$(git log -1 --format="%H %s")
DISK_SIZE_BEFORE=$(du -sh {worktree_path} | cut -f1)

# Save to temp file
cat > /tmp/sandbox-{sandbox_id}-baseline.txt <<EOF
latest_commit: $LATEST_COMMIT
latest_commit_msg: $LATEST_COMMIT_MSG
disk_size_before: $DISK_SIZE_BEFORE
sync_timestamp: $(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
```

(Used in Phase 6 to verify only new commits were added)

---

## PHASE 3: DOCKER IMAGE PREPARATION

### STEP 3.1: Check if lcm-dev-agent image exists

```bash
docker image inspect lcm-dev-agent:latest > /dev/null 2>&1
```

**If exists**: Use existing image → Proceed to STEP 4.1

**If not exists**: Build image
```bash
docker build \
  -t lcm-dev-agent:latest \
  {sandbox_worktree_dir}
```

---

### STEP 3.2: Verify image is functional

```bash
docker run --rm lcm-dev-agent:latest \
  /bin/bash -c "git --version && claude --version && codex --version"
```

Expected: Version strings for all tools

**Action**: Abort if fails — image is broken

---

## PHASE 4: DOCKER CONTAINER LAUNCH & EXECUTION

### STEP 4.1: Prepare environment variables

```bash
ALLOWED_PATHS_JSON=$(jq -c . <<< '{
  "write": ["src/", "tests/"],
  "read": ["*"]
}')

ENV_VARS=(
  "PLAN_ID={plan_id}"
  "RUN_ID={run_id}"
  "SANDBOX_ID={sandbox_id}"
  "ALLOWED_PATHS=${ALLOWED_PATHS_JSON}"
  "TIMEOUT_MINUTES={timeout_minutes}"
  "BRANCH_NAME={branch_name}"
)
```

---

### STEP 4.2: Prepare volume mounts

```bash
# Verify both exist and are readable
test -d {worktree_path} || exit 1
test -d {repo_path}/.git || exit 1
test -r {worktree_path} || exit 1
test -r {repo_path}/.git || exit 1

# Mount pairs:
# {worktree_path} ↔ /workspace
# {repo_path}/.git ↔ /workspace/.git-parent
```

---

### STEP 4.3: Launch Docker container

```bash
docker run \
  --name {sandbox_id} \
  --rm \
  --label plan_id={plan_id} \
  --label run_id={run_id} \
  --label sandbox_id={sandbox_id} \
  --timeout {timeout_minutes*60} \
  -v {worktree_path}:/workspace \
  -v {repo_path}/.git:/workspace/.git-parent \
  -e PLAN_ID={plan_id} \
  -e RUN_ID={run_id} \
  -e SANDBOX_ID={sandbox_id} \
  -e ALLOWED_PATHS="${ALLOWED_PATHS_JSON}" \
  -e TIMEOUT_MINUTES={timeout_minutes} \
  -e BRANCH_NAME={branch_name} \
  lcm-dev-agent:latest \
  /entrypoint.sh
```

**Capture startup**:
```bash
docker logs {sandbox_id} > /tmp/container_startup_${sandbox_id}.log
```

---

### STEP 4.4: Verify container is running

```bash
sleep 2  # Give container time to start
docker ps --filter "name={sandbox_id}" | grep -q {sandbox_id}
```

Expected: Container appears in `docker ps` output

**Action**: Abort if container not running (entrypoint failed)

---

### STEP 4.5: Container entrypoint executes (INSIDE CONTAINER)

#### 4.5.1: Parse and validate env vars

```bash
# Verify all required vars are set
test -n "$PLAN_ID" || exit 1
test -n "$RUN_ID" || exit 1
test -n "$SANDBOX_ID" || exit 1
test -n "$ALLOWED_PATHS" || exit 1

# Verify ALLOWED_PATHS is valid JSON
echo "$ALLOWED_PATHS" | jq empty || exit 1

# Log config
cat > /tmp/sandbox-config.json <<EOF
{
  "plan_id": "$PLAN_ID",
  "run_id": "$RUN_ID",
  "sandbox_id": "$SANDBOX_ID",
  "timeout_minutes": $TIMEOUT_MINUTES,
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
```

---

#### 4.5.2: Lock down all files to read-only

```bash
chmod -R 644 /workspace
chmod -R 755 /workspace/.git  # Git dirs need to be executable
```

---

#### 4.5.3: Set allowed_paths to writable

```bash
ALLOWED_PATHS_JSON="$ALLOWED_PATHS"
WRITE_PATHS=$(echo "$ALLOWED_PATHS_JSON" | jq -r '.write[]')

for path in $WRITE_PATHS; do
  FULL_PATH="/workspace/${path}"

  if [ -d "$FULL_PATH" ]; then
    chmod 755 "$FULL_PATH"
    find "$FULL_PATH" -type f -exec chmod 644 {} \;
    chown -R aiagent:agentgroup "$FULL_PATH"
  elif [ -f "$FULL_PATH" ]; then
    chmod 644 "$FULL_PATH"
    chown aiagent:agentgroup "$FULL_PATH"
  fi
done
```

---

#### 4.5.4: Verify git is accessible

```bash
sudo -u aiagent git -C /workspace status
```

Expected: Shows branch and git status (no errors)

---

#### 4.5.5: Configure git safety

```bash
git config --global safe.directory /workspace
```

(Allows aiagent to use git on mounted volume owned by host)

---

#### 4.5.6: Install pre-push hook

```bash
cat > /workspace/.git/hooks/pre-push <<'EOF'
#!/bin/bash
echo "ERROR: Pushing to origin is not allowed in sandbox"
exit 1
EOF

chmod +x /workspace/.git/hooks/pre-push
```

---

#### 4.5.7: Write sandbox manifest

```bash
cat > /workspace/.sandbox-manifest.json <<EOF
{
  "plan_id": "$PLAN_ID",
  "run_id": "$RUN_ID",
  "sandbox_id": "$SANDBOX_ID",
  "started_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "timeout_minutes": $TIMEOUT_MINUTES,
  "timeout_at": "$(date -u -d "+$TIMEOUT_MINUTES minutes" +%Y-%m-%dT%H:%M:%SZ)",
  "branch_name": "$BRANCH_NAME",
  "allowed_paths": $ALLOWED_PATHS,
  "container_user": "aiagent",
  "container_id": "$(hostname)"
}
EOF
```

---

#### 4.5.8: Switch to aiagent user

```bash
su - aiagent
export PS1="[sandbox] \$ "
cd /workspace
```

---

#### 4.5.9: Verify git state

```bash
git status
git log -1 --oneline
```

Expected: Clean working directory, on correct branch, latest commit visible

---

#### 4.5.10: Drop to interactive bash

```bash
/bin/bash -i
```

(User can now control agent interactively)

---

## PHASE 5: AGENT EXECUTION

User interacts with agent inside container. No automated steps.

---

## PHASE 6: ARTIFACT CAPTURE & CLEANUP (After Container Exits)

### STEP 6.1: Capture container exit status

```bash
docker wait {sandbox_id}
EXIT_CODE=$?
```

---

### STEP 6.2: Capture container logs

```bash
docker logs {sandbox_id} > /tmp/container_logs_${sandbox_id}.txt 2>&1
```

---

### STEP 6.3: Capture git state from worktree (HOST)

```bash
cd {worktree_path}

# Get current HEAD
FINAL_HEAD=$(git rev-parse HEAD)

# List new commits since baseline
git log ${LATEST_COMMIT}..HEAD \
  --format="%H %an %ai %s" > /tmp/new_commits_${sandbox_id}.txt

# Generate diff
git diff ${LATEST_COMMIT}..HEAD \
  > /tmp/changes_${sandbox_id}.patch

# Get status (uncommitted changes)
git status --porcelain > /tmp/git_status_${sandbox_id}.txt

# Check for orphaned commits (should be empty)
git log origin/main..HEAD --format="%H" > /tmp/orphaned_${sandbox_id}.txt
```

---

### STEP 6.4: Validate changes are within allowed_paths

```bash
# Get all modified files from diff
MODIFIED_FILES=$(git diff ${LATEST_COMMIT}..HEAD --name-only)

VIOLATIONS=()
for file in $MODIFIED_FILES; do
  IS_ALLOWED=0
  for allowed_path in "${ALLOWED_WRITE_PATHS[@]}"; do
    if [[ "$file" == "$allowed_path"* ]] || [[ "$allowed_path" == "*" ]]; then
      IS_ALLOWED=1
      break
    fi
  done

  if [ $IS_ALLOWED -eq 0 ]; then
    VIOLATIONS+=("$file")
  fi
done

if [ ${#VIOLATIONS[@]} -gt 0 ]; then
  # Log violations (shouldn't happen if ACLs correct)
  echo "VIOLATIONS_DETECTED: ${VIOLATIONS[@]}" > /tmp/violations_${sandbox_id}.txt
  ARTIFACT_STATUS="violation_detected"
else
  ARTIFACT_STATUS="completed"
fi
```

---

### STEP 6.5: Calculate stats

```bash
DISK_SIZE_AFTER=$(du -sh {worktree_path} | cut -f1)
NUM_NEW_COMMITS=$(git log ${LATEST_COMMIT}..HEAD --oneline | wc -l)

# Parse diff stats
git diff --stat ${LATEST_COMMIT}..HEAD > /tmp/diff_stat_${sandbox_id}.txt
FILES_MODIFIED=$(git diff --stat ${LATEST_COMMIT}..HEAD | tail -1 | awk '{print $1}')
INSERTIONS=$(git diff --stat ${LATEST_COMMIT}..HEAD | tail -1 | awk '{print $4}' | tr -d '+')
DELETIONS=$(git diff --stat ${LATEST_COMMIT}..HEAD | tail -1 | awk '{print $6}' | tr -d '-')
```

---

### STEP 6.6: Clean up Docker container

```bash
docker rm {sandbox_id}  # Automatic with --rm flag
```

(Container and filesystem deleted, mounts on host persist)

---

### STEP 6.7: Archive artifacts

```bash
ARCHIVE_DIR="/archive/sandbox/${sandbox_id}"
mkdir -p "$ARCHIVE_DIR"

cp /tmp/container_logs_${sandbox_id}.txt "$ARCHIVE_DIR/"
cp /tmp/new_commits_${sandbox_id}.txt "$ARCHIVE_DIR/"
cp /tmp/changes_${sandbox_id}.patch "$ARCHIVE_DIR/"
cp /tmp/git_status_${sandbox_id}.txt "$ARCHIVE_DIR/"
cp {worktree_path}/.sandbox-manifest.json "$ARCHIVE_DIR/"
```

---

### STEP 6.8: Store in AIDevOps DB

```sql
INSERT INTO agent_artifacts (
  artifact_id, run_id, plan_id, sandbox_id,
  artifact_type, status, exit_code,
  started_at, completed_at, duration_seconds,
  num_commits, files_modified, lines_added, lines_deleted,
  content_url, metadata, created_at
) VALUES (
  uuid_generate_v4(),
  '{run_id}',
  '{plan_id}',
  '{sandbox_id}',
  'sandbox_execution',
  '{ARTIFACT_STATUS}',
  {EXIT_CODE},
  '{container_start_time}',
  NOW(),
  {elapsed_seconds},
  {NUM_NEW_COMMITS},
  {FILES_MODIFIED},
  {INSERTIONS},
  {DELETIONS},
  's3://artifacts/sandbox/{sandbox_id}/',
  jsonb_build_object(
    'violations', array[{VIOLATIONS}],
    'final_head', '{FINAL_HEAD}',
    'disk_size_before', '{DISK_SIZE_BEFORE}',
    'disk_size_after', '{DISK_SIZE_AFTER}',
    'branch', '{branch_name}'
  ),
  NOW()
);
```

---

### STEP 6.9: Store audit event

```sql
INSERT INTO audit_events (
  event_id, event_type, plan_id, run_id, sandbox_id,
  status, summary, actor, timestamp, details
) VALUES (
  uuid_generate_v4(),
  'sandbox_execution_complete',
  '{plan_id}',
  '{run_id}',
  '{sandbox_id}',
  '{ARTIFACT_STATUS}',
  'Sandbox executed: {NUM_NEW_COMMITS} commits, {FILES_MODIFIED} files modified',
  'agent-executor',
  NOW(),
  jsonb_build_object(
    'exit_code', {EXIT_CODE},
    'duration_seconds', {elapsed_seconds},
    'violations', array[{VIOLATIONS}]
  )
);
```

---

### STEP 6.10: Return results to caller

```json
{
  "sandbox_id": "{sandbox_id}",
  "plan_id": "{plan_id}",
  "run_id": "{run_id}",
  "status": "completed|timeout|error",
  "exit_code": 0,
  "started_at": "2026-05-30T14:05:23Z",
  "completed_at": "2026-05-30T14:27:45Z",
  "duration_seconds": 1342,
  "num_commits": 1,
  "files_modified": 5,
  "lines_added": 142,
  "lines_deleted": 89,
  "disk_size_before": "245M",
  "disk_size_after": "247M",
  "artifacts_url": "s3://artifacts/sandbox/{sandbox_id}/",
  "violations": [],
  "worktree_path": "{worktree_path}",
  "branch": "{branch_name}"
}
```

---

## Error Handling & Recovery

### If any step fails:
1. Log detailed error message with step number
2. Store partial artifacts (what was captured so far)
3. Mark artifact status as "error"
4. Store error details in audit trail
5. Return error response with recovery instructions

### Timeout handling:
- Docker timeout kills container automatically
- Capture partial results (commits made so far)
- Mark status as "timeout"
- Preserve worktree for manual cleanup

### Worktree cleanup (after successful merge):
Automatic via webhook when PR merged to main (see SANDBOX-ARCHITECTURE.md Phase 8)
