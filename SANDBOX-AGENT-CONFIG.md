# In-Sandbox Agent Configuration — Design Note

**Status:** Draft, awaiting Phase 2 implementation
**Owner:** Phase 2 entrypoint work
**Scope:** Defines how `claude`, `codex`, and `gemini` are configured **inside** the sandbox container. Does **not** cover the host-side `lcm-sandbox` CLI permissions, which are already implemented.
**Companion:** See `SANDBOX-AGENT-CONFIG.html` for a visual explainer of layered defenses, prompt-flow, and the per-prompt coverage matrix.

---

## The problem in one paragraph

The sandbox container is the isolation boundary. The agent running inside it has no human to answer prompts. If the in-container agent CLI hits a permission prompt — at any layer, for any reason — it will hang until the container timeout fires. That's a 30-to-60-minute dead run with no useful artifacts. Therefore the in-container agent configuration must **be exhaustive about silencing every prompt path**, not just the obvious `defaultMode` setting. This document is the inventory of every known prompt path and the mechanism that silences each one.

---

## Layered defenses

Three independent layers each restrict what the agent can do. The in-sandbox agent config is one layer; the other two give us hard guarantees that let the agent config stay permissive.

| Layer | Controlled by | Granularity | What it stops |
| :---- | :------------ | :---------- | :------------ |
| **OS-level ACLs** (entrypoint chmod/chown, STEP 4.5.2–4.5.3) | Host CLI `--allowed-paths` | Per-path read/write | Agent can't write outside `write:` paths even if it tries |
| **Git pre-push hook** (STEP 4.5.6) | Entrypoint | Push to origin | Agent can't exfiltrate to remote |
| **Container runtime ACL** | Docker `--rm`, no privileged, dropped caps, mounted-only writable paths | Process-level | Agent can't escape the container |
| In-sandbox agent config (this doc) | Bundled into image / rendered by entrypoint | Per-tool prompts | Belt-and-suspenders; the hangs we're preventing |

Because layers 1–3 give us hard guarantees, layer 4 (the agent config) is allowed to default to permissive. The agent can attempt anything; the OS-level ACL refuses what's outside the boundary.

---

## Every prompt path the agent could hit (and how each is silenced)

### Claude Code CLI

| # | Prompt type | Trigger | Silenced by |
| :- | :---------- | :------ | :---------- |
| 1 | Bash command approval | Any Bash call not matched by an `allow` rule | `defaultMode: bypassPermissions` |
| 2 | Edit/Write file approval | Edit or Write to a file not matched by `allow` | `defaultMode: bypassPermissions` |
| 3 | **Overwrite-existing-file dialog** | Write tool targets a file that already exists | **`defaultMode: bypassPermissions` (only — `acceptEdits` does NOT silence this)** |
| 4 | WebFetch domain approval | First fetch to a new domain | `permissions.allow: ["WebFetch(domain:*)"]` + `skipWebFetchPreflight: true` |
| 5 | MCP server approval | First use of a project-local MCP server | `enableAllProjectMcpServers: true` |
| 6 | Bypass-mode acceptance dialog | `defaultMode: bypassPermissions` triggers a one-time "are you sure?" | `skipDangerousModePermissionPrompt: true` |
| 7 | Auto-mode opt-in dialog | If `defaultMode: auto` is ever used | `skipAutoPermissionPrompt: true` (defensive — not used in permissive profile) |
| 8 | **`rm -rf /` circuit breaker** | `Bash(rm -rf /)` or `rm -rf /*` | **NOT bypassable from config.** Mitigated by rm-shim (see below). |
| 9 | **`rm -rf ~` circuit breaker** | `Bash(rm -rf ~)` or `rm -rf $HOME/*` | **NOT bypassable from config.** Mitigated by rm-shim. |
| 10 | PreToolUse hook returning `permissionDecision: "ask"` | Any inherited hook | `disableAllHooks: true` (until specific sandbox hooks are deliberately re-enabled) |
| 11 | First-run auth (OAuth in browser) | Agent invoked without credentials | `ANTHROPIC_API_KEY` env var mounted as secret |
| 12 | Subagent invocation approval | If subagents are enabled and approval is on | Subagent invocations covered by `bypassPermissions`; deny specific subagents at host level if needed |

### Codex CLI — **AUDIT PENDING (hard Phase 2 prerequisite)**

Required answers before Phase 2 Dockerfile work:
- Does Codex have an equivalent to `defaultMode: bypassPermissions`?
- Is there a `CI=true` or `NONINTERACTIVE=1` env var Codex respects?
- How is auth provided? (env var, mounted credential file, OAuth-only?)
- Does Codex have a per-command approval UI that hangs in non-TTY environments?
- If any of the above is missing or unclear, **Codex cannot ship in the permissive profile** until a fix is engineered.

### Gemini CLI — **AUDIT PENDING (hard Phase 2 prerequisite)**

Same audit questions as Codex. Until answered, Gemini is excluded from the permissive profile.

---

## The canonical permissive profile (Claude Code)

This is the config file that gets rendered to `/home/aiagent/.claude/settings.json` inside the container when `LCM_AGENT_PROFILE=permissive` (the default).

```json
{
  "permissions": {
    "defaultMode": "bypassPermissions",
    "allow": [
      "WebFetch(domain:*)"
    ]
  },
  "skipDangerousModePermissionPrompt": true,
  "skipAutoPermissionPrompt": true,
  "skipWebFetchPreflight": true,
  "enableAllProjectMcpServers": true,
  "disableAllHooks": true,
  "fileCheckpointingEnabled": false,
  "autoCompactEnabled": true,
  "spinnerTipsEnabled": false,
  "promptSuggestionEnabled": false
}
```

Every key is silencing a specific prompt path from the table above. Each addition has a rationale; nothing is speculative.

---

## Required container environment variables

```
# Authentication (mounted as Docker secrets at run-time, never baked in)
ANTHROPIC_API_KEY=<from host secret store>
OPENAI_API_KEY=<from host secret store, if Codex>
GEMINI_API_KEY=<from host secret store, if Gemini>

# Non-interactive signaling — many CLIs check these to suppress prompts
CI=true
NONINTERACTIVE=1
DEBIAN_FRONTEND=noninteractive
TERM=dumb

# Profile selector (read by entrypoint's apply_agent_profile.py)
LCM_AGENT_PROFILE=permissive

# Tell the agents they have no terminal
NO_COLOR=1
```

---

## Defensive layers beyond config

### Layer A: `rm` shim for circuit-breaker prompts

Claude Code's `rm -rf /` and `rm -rf ~` circuit breakers fire even under `bypassPermissions`. Inside the container `$HOME=/home/aiagent`, so legitimate operations like `rm -rf ~/.cache/pip` will hang.

**Mitigation:** install `/usr/local/bin/rm` as a small wrapper script that:
1. Refuses `rm -rf /`, `rm -rf /*`, `rm -rf ~`, `rm -rf ~/`, `rm -rf $HOME` directly (exits 1 with a clear message). Claude Code never sees these calls, so its circuit breaker never fires.
2. For everything else, exec's the real `/bin/rm`.

This is a security improvement and a hang-prevention measure in one. The shim is the agent's only `rm` because `/usr/local/bin` precedes `/bin` in `PATH`.

### Layer B: stdin-EOF defensive shim

If any prompt slips through anyway (unknown CLI, new prompt type, hook misconfig), the agent process will block on stdin read. Wrap the agent invocation:

```bash
# Inside entrypoint, when launching the agent:
exec setsid </dev/null timeout --signal=TERM --kill-after=10s \
  ${TIMEOUT_MINUTES}m \
  agent-cli "$@"
```

- `</dev/null` — stdin is closed; any prompt that reads stdin gets immediate EOF and the agent exits rather than hanging.
- `setsid` — detach from the controlling terminal so no TTY-based prompt can succeed.
- `timeout` — hard wall-clock kill matching the sandbox `--timeout`.

This means: **if anything slips past our config, the agent fails fast (within seconds) rather than hanging for the full timeout.** Failure is preferable to silent hang because Phase 6 (artifact capture) still gets useful logs.

### Layer C: pre-flight smoke test

The entrypoint should, before handing off to the agent, run a dry-run that probes the most-likely prompt sources:

```bash
# In entrypoint, after applying profile, before agent launch:
claude --print "say ok" </dev/null
# If this hangs more than 30s, something in the config is wrong; abort before
# the real agent run wastes the timeout budget.
```

If the smoke test fails, the entrypoint writes a clear error to `/workspace/.sandbox-bootstrap-error.txt` and exits non-zero, which Phase 6 captures cleanly.

---

## Per-agent posture (revised with audit dependencies)

| Agent | Config location | Profile status | Blocking question for Phase 2 |
| :---- | :--------------- | :------------- | :---------------------------- |
| Claude Code | `/home/aiagent/.claude/settings.json` | **Ready** — full template above | None |
| Codex | TBD | **Blocked** — audit required | Does it support non-interactive mode equivalent to `bypassPermissions`? |
| Gemini | TBD | **Blocked** — audit required | Same as Codex |

If the audit reveals Codex or Gemini cannot run prompt-free, two options: (a) include them but document that the agent must avoid the prompt-triggering paths (fragile); (b) exclude them from the permissive profile and ship Claude-only first. **(b) is the recommended fallback.**

---

## What `standard` profile means (the override)

`LCM_AGENT_PROFILE=standard` for niche audit scenarios where every file edit should be logged-but-allowed without other relaxations:

```json
{
  "permissions": {
    "defaultMode": "acceptEdits",
    "allow": ["WebFetch(domain:*)", "Bash(*)"]
  },
  "skipDangerousModePermissionPrompt": true,
  "skipWebFetchPreflight": true,
  "enableAllProjectMcpServers": true,
  "disableAllHooks": true
}
```

Note: even `standard` needs `Bash(*)` in allow, because `acceptEdits` alone does not silence Bash prompts. Without `Bash(*)`, the agent hangs on the first Bash call.

The `standard` profile is documented as the rare case; the default and recommended path is `permissive`.

---

## How the entrypoint applies the profile

Between STEP 4.5.7 (write sandbox manifest) and STEP 4.5.8 (switch to aiagent user):

```bash
# STEP 4.5.7a: validate profile choice
PROFILE="${LCM_AGENT_PROFILE:-permissive}"
case "$PROFILE" in
  permissive|standard) ;;
  *) echo "ERROR: unknown LCM_AGENT_PROFILE=$PROFILE" >&2; exit 1 ;;
esac

# STEP 4.5.7b: render config templates to the aiagent home
python3 /opt/lcm-sandbox/apply_agent_profile.py \
  --profile "$PROFILE" \
  --target /home/aiagent

# STEP 4.5.7c: install rm-shim
install -m 0755 /opt/lcm-sandbox/rm-shim.sh /usr/local/bin/rm

# STEP 4.5.7d: bootstrap smoke test
/opt/lcm-sandbox/smoke-test.sh \
  || { echo "Agent bootstrap failed"; exit 1; }
```

The Phase 2 deliverable includes:
- `apply_agent_profile.py` — renders configs from JSON templates in `agent_profiles/`
- `rm-shim.sh` — the wrapper described above
- `smoke-test.sh` — the bootstrap probe

---

## Open questions for Phase 2 (hard prerequisites)

1. **Codex audit.** Does Codex have a non-interactive mode? Document the answer in this file. **Blocks Codex inclusion in the image.**
2. **Gemini audit.** Same as Codex. **Blocks Gemini inclusion in the image.**
3. **Confirm the rm-shim approach doesn't break tools that legitimately need to clean their own caches** (test by running `pip install` and verifying its cleanup steps work).
4. **Confirm `disableAllHooks: true` doesn't break the in-container observability** (we may want our own audit hooks; reconcile during Phase 2).
5. **Decide whether the smoke test should also probe a Bash command, an Edit, and a WebFetch** to verify each silenced path works end-to-end. Recommendation: yes, probe all four.
