# Sandbox Control Schema
**File:** `/Users/liborballaty/LocalProjects/GitHubProjectsDocuments/LCM-Sandbox/SANDBOX-CONTROL-SCHEMA.md`
**Description:** Canonical wire-format schemas for the agentic sandbox control plane. Both Phase A (filesystem polling) and Phase B (MCP Streamable HTTP) transports carry these same shapes so consumers do not break across the transition.
**Author:** Libor Ballaty <libor@arionetworks.com>
**Created:** 2026-06-19

---

## Status

| Schema | Stability | Consumed by |
|---|---|---|
| `plan.json` | v1 — stable contract | AIDevOps (writer), Hermes (reader), agent (via Hermes) |
| `status.json` | v1 — stable contract | Hermes (writer), AIDevOps (reader) |
| `events.jsonl` | v1 — stable contract | Hermes (writer, append-only), AIDevOps (tailer) |
| `inbox/<msg-id>.json` | v1 — stable contract | AIDevOps (writer), Hermes (reader) |
| `outbox/<msg-id>.json` | v1 — stable contract | Hermes (writer), AIDevOps (reader) |
| Hermes local HTTP endpoints | v1 — stable contract | agent (caller), Hermes (handler) |

All shapes carry a `schema_version` field. Breaking changes bump the version; consumers must check.

---

## Purpose

This doc is the **single source of truth** for the wire-format contract between AIDevOps and the agentic sandbox. It is referenced by:
- `SANDBOX-CONTROL-PLANE.html` (architecture and flows)
- `SANDBOX-ORCHESTRATION.html` (Phase B MCP transport)
- aidevops `TODO.md` #111 (Phase A implementation), #113 (AIDevOps integration endpoints)

When Phase A (filesystem polling) and Phase B (MCP Streamable HTTP) both exist, they MUST carry these same shapes. Phase A writes them as files on the host; Phase B streams them over MCP. Consumers that learn one transport's shapes do not break when the other lands.

---

## Filesystem layout (Phase A reference)

```
~/.lcm-sandbox/runs/<sandbox-id>/
├── plan/
│   ├── plan.json         delivered by AIDevOps before docker run
│   ├── plan.md           human-readable plan body
│   └── inputs/           supporting files (fixtures, configs)
├── status.json           written continuously by Hermes
├── events.jsonl          append-only audit log
├── inbox/                AIDevOps drops messages here
│   └── <msg-id>.json
├── outbox/               Hermes drops messages here
│   └── <msg-id>.json
└── hermes/
    ├── llm-calls.jsonl   per-LLM-call audit
    └── policy-log.jsonl  classification policy decisions
```

The directory itself is bind-mounted into the container at `/control` (Hermes-only). The agent never sees `/control`.

---

## `plan.json`

Written once by AIDevOps before docker launch. Never mutated after launch.

```json
{
  "schema_version":  "1",
  "plan_id":         "plan_abc123",
  "run_id":          "run_def456",
  "title":           "Implement TODO 88",
  "repo_kind":       "existing",
  "instructions_md_path": "/control/plan/plan.md",

  "context": {
    "repo":             "/workspace",
    "branch":           "feature/auto-todo-88",
    "source_revision":  "abc123def"
  },

  "scope": {
    "modules":       ["parser"],
    "include_tests": true,
    "include_docs":  false
  },

  "required_paths": {
    "rw": ["src/parser/", "tests/parser/"]
  },

  "read_exclusions": [
    ".env*", "secrets/", "credentials/",
    "node_modules/", "vendor/",
    "dist/", "build/"
  ],

  "exit_criteria": [
    { "type": "tests_pass",       "command": "pnpm test" },
    { "type": "non_regression",   "command": "pnpm test:non-regression" },
    { "type": "commit_count_min", "value":   1 }
  ],

  "checkpoint_policy": {
    "on_blocked":               "emit_event_and_wait",
    "on_failure":               "emit_event_and_exit",
    "on_uncertain_destructive": "ask_via_outbox"
  },

  "operator": {
    "user_id":          "libor@labguide.io",
    "respond_endpoint": "https://aidevops.local/api/agentic-sandboxes/run_def456/respond"
  }
}
```

### Field semantics

| Field | Type | Required | Notes |
|---|---|---|---|
| `schema_version` | string | yes | Currently `"1"`. |
| `plan_id` | string | yes | Stable plan identifier from AIDevOps. |
| `run_id` | string | yes | Per-execution identifier. Used in container name, control dir path, and event correlation. |
| `title` | string | yes | Human-readable summary. |
| `repo_kind` | enum `"new" \| "existing"` | yes | Drives Hermes default policy. `existing` activates the non-regression instruction. |
| `instructions_md_path` | string | yes | Container path (always under `/control/plan/`) where the full plan body lives in markdown. |
| `context.repo` | string | yes | Container path of the worktree mount. Almost always `/workspace`. |
| `context.branch` | string | yes | Branch the worktree pins to. |
| `context.source_revision` | string | yes | Specific commit hash. The worktree is pinned to this; never floating `HEAD`. |
| `scope.modules` | string[] | no | Logical module names per the target repo's `docs/REPO-TOPOLOGY.md`. If present, the launcher derives `required_paths` from topology. |
| `scope.include_tests` | bool | no | Adds module test dirs to derived `required_paths.rw`. |
| `scope.include_docs` | bool | no | Adds module docs to derived `required_paths.rw`. |
| `required_paths.rw` | string[] | yes | Worktree-relative paths the agent may modify. Either explicit or derived from `scope`. |
| `read_exclusions` | string[] | yes | Patterns excluded from sparse checkout. Secrets, vendored deps, build artifacts. |
| `exit_criteria` | object[] | yes | Conditions evaluated before `tasks_complete` can be emitted. See "Exit criteria types" below. |
| `checkpoint_policy.on_blocked` | enum | yes | `"emit_event_and_wait"` (default) — agent emits blocked event and waits for inbox response. |
| `checkpoint_policy.on_failure` | enum | yes | `"emit_event_and_exit"` (default) — agent emits failed event and exits. |
| `checkpoint_policy.on_uncertain_destructive` | enum | yes | `"ask_via_outbox"` (default) — agent must ask before destructive ops. |
| `operator.user_id` | string | yes | Who approved the plan. Surfaces in audit events. |
| `operator.respond_endpoint` | string | yes | Phase B callback URL; ignored in Phase A. |

### Exit criteria types

| `type` | Fields | Meaning |
|---|---|---|
| `tests_pass` | `command` | Run `command`; pass iff exit 0. |
| `non_regression` | `command` | Active for `repo_kind: existing`. Run `command` against untouched code paths; pass iff no regressions. |
| `lint_pass` | `command` | Run `command`; pass iff exit 0. |
| `typecheck_pass` | `command` | Run `command`; pass iff exit 0. |
| `commit_count_min` | `value` (int) | Pass iff agent made at least `value` commits in the worktree. |
| `files_changed_min` | `value` (int) | Pass iff at least `value` files changed. |
| `custom` | `command`, `description` | Free-form; runs `command`, pass iff exit 0. Surfaced as `description` in UI. |

---

## `status.json`

Written continuously by Hermes. Latest-only (overwrites previous).

```json
{
  "schema_version": "1",
  "run_id":         "run_def456",
  "phase":          "executing",
  "current_step":   "Add unit tests for parser",
  "step_index":     3,
  "step_total":     7,
  "last_action_ts": "2026-06-19T14:23:01Z",
  "heartbeat":      "2026-06-19T14:23:09Z",
  "blocked_on":     null,
  "exit_code":      null,
  "manifest": {
    "commits":       ["a1b2c3d"],
    "files_changed": ["src/parser.ts", "tests/parser.spec.ts"]
  }
}
```

### Field semantics

| Field | Type | Required | Notes |
|---|---|---|---|
| `schema_version` | string | yes | Currently `"1"`. |
| `run_id` | string | yes | Matches `plan.run_id`. |
| `phase` | enum | yes | One of: `"starting"`, `"executing"`, `"blocked"`, `"complete"`, `"failed"`, `"stalled"`. |
| `current_step` | string | yes | Human-readable description of what the agent is doing now. |
| `step_index` | int | no | 1-based current step index (if the agent tracks steps). |
| `step_total` | int | no | Total expected steps. May change as agent re-plans. |
| `last_action_ts` | ISO 8601 | yes | When the last meaningful action happened. |
| `heartbeat` | ISO 8601 | yes | Updated every ≤10s by Hermes regardless of agent activity. AIDevOps marks the run `stalled` if `heartbeat` is older than 60s. |
| `blocked_on` | string \| null | yes | When `phase == "blocked"`, the `msg_id` of the outbox message awaiting response. |
| `exit_code` | int \| null | yes | Set only when `phase == "complete"` or `"failed"`. 0 = success. |
| `manifest.commits` | string[] | yes | Commit SHAs the agent made (incremental; appended as work progresses). |
| `manifest.files_changed` | string[] | yes | Worktree-relative paths modified. |

### Phase state machine

```
starting → executing → (blocked ↔ executing) → complete | failed
                                              ↑
                                              stalled (detected externally by AIDevOps via heartbeat timeout)
```

`stalled` is set by the AIDevOps poller, not by Hermes. Hermes only writes the first four phases.

---

## `events.jsonl`

Append-only audit log. One JSON object per line. Monotonically increasing `seq`.

```jsonl
{"seq":1, "ts":"2026-06-19T14:20:00Z", "type":"launched",      "payload":{}}
{"seq":2, "ts":"2026-06-19T14:20:02Z", "type":"plan_loaded",   "payload":{"plan_id":"plan_abc123"}}
{"seq":3, "ts":"2026-06-19T14:20:05Z", "type":"step_started",  "payload":{"step":1, "title":"Read parser"}}
{"seq":4, "ts":"2026-06-19T14:21:30Z", "type":"file_edited",   "payload":{"path":"src/parser.ts", "lines_added":12, "lines_removed":3}}
{"seq":5, "ts":"2026-06-19T14:22:00Z", "type":"commit",        "payload":{"sha":"a1b2c3d", "message":"refactor parser"}}
{"seq":6, "ts":"2026-06-19T14:23:00Z", "type":"blocked",       "payload":{"msg_id":"m_001"}}
{"seq":7, "ts":"2026-06-19T14:24:10Z", "type":"unblocked",     "payload":{"msg_id":"m_001", "answer":"yes"}}
{"seq":8, "ts":"2026-06-19T14:30:00Z", "type":"tasks_complete","payload":{"exit_criteria_met":["tests_pass","non_regression"]}}
```

### Event types

| `type` | Payload | Meaning |
|---|---|---|
| `launched` | `{}` | Container started, Hermes booted. |
| `plan_loaded` | `{plan_id}` | Plan read successfully. |
| `step_started` | `{step, title}` | Agent begins a logical step. |
| `step_completed` | `{step}` | Agent completes a step. |
| `file_edited` | `{path, lines_added, lines_removed}` | Agent edited a file. |
| `commit` | `{sha, message}` | Agent committed in the worktree. |
| `tool_call` | `{tool, args_summary, result}` | Agent invoked a Hermes-mediated tool. |
| `tool_refused` | `{tool, args_summary, reason}` | Hermes refused a tool call. Important for audit. |
| `llm_call` | `{model, input_tokens, output_tokens, classification}` | Hermes proxied an LLM call. |
| `policy_decision` | `{policy, decision, target}` | Classification policy applied to outbound payload. |
| `blocked` | `{msg_id}` | Agent emitted ask via outbox, waiting for inbox response. |
| `unblocked` | `{msg_id, answer}` | Inbox response received, agent continues. |
| `heartbeat` | `{}` | Periodic liveness signal (also reflected in `status.heartbeat`). |
| `tasks_complete` | `{exit_criteria_met[]}` | Agent declares completion. AIDevOps verifies exit criteria. |
| `failed` | `{reason, error, partial_progress}` | Agent declares failure. |
| `regression_detected` | `{verification_command, output}` | Active for `existing` repos. Non-regression check failed. |

### Schema constraints

- `seq` MUST be monotonically increasing per run (no gaps, no reorder).
- `ts` MUST be ISO 8601 UTC.
- `type` MUST be a known type (above). Unknown types are tolerated by consumers but logged as warnings.
- `payload` may carry arbitrary additional fields beyond what's documented; consumers must ignore unknowns.

---

## `inbox/<msg-id>.json` and `outbox/<msg-id>.json`

Bidirectional message channel. Hermes writes outbox; AIDevOps writes inbox; both keyed by the same `msg_id`.

### Outbox (Hermes → AIDevOps)

```json
{
  "msg_id":  "m_001",
  "type":    "ask",
  "ts":      "2026-06-19T14:23:00Z",
  "question": "Should we adopt strict mode in tsconfig?",
  "options":  ["yes", "no", "only for new files"],
  "context":  "Existing code has 14 type errors that would surface."
}
```

### Inbox (AIDevOps → Hermes)

```json
{
  "msg_id": "m_001",
  "type":   "answer",
  "ts":     "2026-06-19T14:24:00Z",
  "value":  "yes",
  "actor":  "libor@labguide.io"
}
```

### Message types

| Direction | `type` | Fields | Meaning |
|---|---|---|---|
| outbox | `ask` | `question`, `options[]`, `context` | Agent needs operator input. Blocks until answered or timeout. |
| outbox | `request_credential` | `credential_name`, `purpose` | Agent needs a credential not provided in plan. |
| outbox | `request_scope_extension` | `paths[]`, `reason` | Agent needs to write outside `required_paths.rw`. Operator may approve, deny, or amend. |
| outbox | `notify` | `notification_type`, `body` | Informational; no response expected. |
| inbox | `answer` | `value`, `actor` | Response to outbox `ask`. `value` is one of `options[]` or free-form if `options` was empty. |
| inbox | `credential` | `name`, `value`, `actor` | Response to `request_credential`. Hermes injects into agent's environment. |
| inbox | `scope_amendment` | `rw_additions[]`, `actor` | Response to `request_scope_extension`. Hermes updates the live `required_paths` and re-applies mount config (Phase B may live-remount; Phase A requires restart). |
| inbox | `cancel` | `reason`, `actor` | Operator-initiated cancellation. Agent should clean up and emit `failed` with reason. |

### Timeouts

- Outbox `ask` and `request_credential` default to 30-minute timeout. Plan may override via `checkpoint_policy.ask_timeout_seconds`.
- Timeout produces an inbox response with `type: "timeout"` and the agent receives an error from its blocking call.

### Garbage collection

- Resolved messages (both inbox and outbox) remain in the directories for audit. Not deleted by Hermes or AIDevOps during the run.
- Run-end cleanup may archive both directories to `~/.lcm-sandbox/artifacts/<sandbox-id>/control/`.

---

## Hermes local HTTP endpoints (agent ↔ Hermes)

These are the in-container endpoints the agent calls. The base URL is fixed: `http://localhost:8765/v1/`. Hermes does NOT expose these outside the container.

### Plan retrieval

```
GET /v1/plan
→ 200 {
    title:                    string,
    instructions_markdown:    string,
    repo_kind:                "new" | "existing",
    exit_criteria:            object[],
    required_paths:           {rw: string[]},
    read_exclusions:          string[],
    permitted_actions:        string[],     // e.g. ["read", "write_in_rw_paths", "commit", "run_test_commands"]
    ask_endpoint:             "/v1/ask",
    notify_endpoint:          "/v1/notify"
  }
```

Hermes returns a redacted view — it strips `operator.respond_endpoint` and any other operator-private fields.

### Topology resolution (when target repo declares `docs/REPO-TOPOLOGY.md`)

```
GET /v1/topology
→ 200 {full resolved topology, same shape as the repo's docs/REPO-TOPOLOGY.md}

GET /v1/topology/module/:name
→ 200 {code: string, tests: string}  or  404

GET /v1/topology/commands
→ 200 {test: string, lint: string, ...}
```

### Ask (blocking; Hermes mediates to operator)

```
POST /v1/ask
Body: {
  question: string,
  options:  string[]?,
  context:  string?
}
→ 200 {answer: string, msg_id: string}     // when operator responds
→ 408 {error: "timeout", msg_id: string}   // on timeout
→ 503 {error: "cancelled", msg_id: string} // if run is cancelled
```

### Notify (non-blocking)

```
POST /v1/notify
Body: {
  type:    string,    // matches an events.jsonl event type
  payload: object
}
→ 202 {seq: int}
```

### Tool invocation (LLM-mediated)

```
POST /v1/tool/<tool-name>
Body: {tool-specific arguments}
→ 200 {tool-specific result}
→ 403 {error: "refused", reason: string, msg_id: string?}  // if outside scope; Hermes records to events
```

The `<tool-name>` set is closed and defined by Hermes:
- `read_file`, `write_file`, `list_dir`, `glob` (filesystem)
- `run_command` (Hermes runs the command, captures output, returns)
- `commit` (Hermes runs `git add` and `git commit` in worktree)
- `ask` (sugar over `POST /v1/ask`)

Direct filesystem syscalls bypassing these tools are NOT intercepted by Hermes. The mount layer and container hardening are the enforcement when the agent goes around Hermes.

### LLM proxy (egress with classification)

```
POST /v1/llm/messages
Body: {model: string, messages: object[], ...}
→ 200 {Anthropic / OpenAI-compatible response}
→ 403 {error: "classification_block", classification: string, redaction_proposal: object?}
```

Hermes applies the data classification policy from `aidevops/design/LLM-DATA-CLASSIFICATION-POLICY.md` to both prompts and responses. Sensitive content may be blocked, redacted, or require operator approval before egress.

---

## Versioning policy

- Bumping `schema_version` is breaking. New fields without bumping is non-breaking; consumers ignore unknowns.
- Renaming or removing a field is breaking.
- Changing semantics without renaming is breaking.
- Hermes and AIDevOps SHOULD log the version they expect at startup and refuse to operate when the plan's `schema_version` is unknown.

---

## Optional fields (non-breaking extensions)

### `scaffolding_actions[]` on `plan.json` (added 2026-06-25)

Used by the `lcm-sandbox scaffold` deterministic executor (RCW-4 Slice B v0). When present, the executor reads this list instead of going through the Hermes LLM-agent loop and executes filesystem operations directly. Hermes runs ignore this field.

```json
{
  "schema_version": "1",
  "scaffolding_actions": [
    { "action": "write_file", "path": "README.md", "content": "..." },
    { "action": "write_file", "path": "AGENTS.md", "content": "..." },
    { "action": "git_init" },
    { "action": "git_commit", "message": "Initial scaffold" }
  ]
}
```

Per-action fields:

| Action | Required fields | Optional fields | Notes |
|---|---|---|---|
| `write_file` | `path` (relative to target dir), `content` | — | Creates parent directories as needed. |
| `git_init` | — | — | Runs `git init` in the target directory. |
| `git_commit` | — | `message` (default `"Initial scaffold"`) | Runs `git add -A` then `git commit -m <message>`. |

Per-action runtime state (mutated by the executor and surfaced in `status.json.scaffolding_actions[]`):

| Field | Type | Notes |
|---|---|---|
| `status` | `pending` \| `running` \| `done` \| `failed` | Set by the executor as the action progresses. |
| `error_message` | string | Set only when `status === 'failed'`. |

Events emitted to `events.jsonl` during scaffolding (reuses existing known types):

- `step_started` with `{ index, action, path }`
- `step_completed` with `{ index, action }`
- `failed` with `{ index, action, error }`
- `tasks_complete` with `{ actions_completed }`

---

## Cross-references

- Architecture and flows: `SANDBOX-CONTROL-PLANE.html`
- Phase B MCP transport (carries these same shapes live): `SANDBOX-ORCHESTRATION.md` + `.html`
- aidevops Phase A implementation: TODO #111
- aidevops integration endpoints: TODO #113
- LLM classification policy: `aidevops/design/LLM-DATA-CLASSIFICATION-POLICY.md`
- Persona work: `aidevops/design/HERMES-PERSONA-INTEGRATION-PLAN.md`
- Scaffolding executor (RCW-4 Slice B v0): `lcm_sandbox/commands/scaffold.py`; aidevops integration in `server/services/repo-creation/executor.js`
