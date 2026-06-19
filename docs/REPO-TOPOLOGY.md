# Repository Topology
**File:** `/Users/liborballaty/LocalProjects/GitHubProjectsDocuments/LCM-Sandbox/docs/REPO-TOPOLOGY.md`
**Description:** Repo-local topology contract for LCM-Sandbox — declares roots, code/test/scripts layout, build and test commands, secrets and vendored locations, and write-coordination policy, so agents and documentation workflows can resolve repo-specific paths without re-discovery
**Author:** Libor Ballaty <libor@arionetworks.com>
**Created:** 2026-06-19

---

## Purpose

LCM-Sandbox is a flat Python repo with a primary CLI package and supporting bash scripts for the manual dev-sandbox flow. Global agent workflows should treat this file as the topology contract that explains:
- where code, tests, scripts, and docs live,
- which build and test commands the repo uses,
- which paths must be excluded from agent read scope (secrets, vendored deps, build artifacts),
- write-coordination policy.

This file extends the global contract at `~/.ai-dev-dotfiles/REPO-DOCUMENTATION-TOPOLOGY-CONTRACT.md` with the structural fields needed by the agentic sandbox design (`aidevops/design/TODO.md` #115).

---

## Topology Summary

### Root model

| Concept | Relative path | Meaning |
|---|---|---|
| `repo_root` | `.` | Flat repo, no nested primary app |
| `project_root` | `.` | Same as repo root |
| `code_root` | `lcm_sandbox/` | Primary Python package (CLI + library) |
| `tests_root` | `lcm_sandbox/tests/` | Pytest suite |
| `scripts_root` | `scripts/` | Bash + Python helper scripts (dev-sandbox flow, Docker entrypoints, agent profile applier) |
| `repo_docs_root` | `.` (root-level) | Design and architecture docs live at repo root (`SANDBOX-*.md`, `*.html`); `docs/` holds only ad-hoc verification artifacts |
| `tracker_root` | n/a | Trackers live in `aidevops/design/TODO.md`; LCM-Sandbox does not maintain its own tracker |

### Stack

| Field | Value |
|---|---|
| `stack` | Python service + CLI |
| `language` | Python (>= 3.10) |
| `package_manager` | pip / uv |
| `manifest` | `pyproject.toml` |
| `entry_points` | `lcm-sandbox`, `persona-state-renderer`, `persona-state-capturer` (declared in `pyproject.toml [project.scripts]`) |

### Commands

| Command kind | Value | Notes |
|---|---|---|
| `commands.test` | `pytest` | Runs from repo root; pytest config in `pyproject.toml` |
| `commands.test_one` | `pytest <path>` | Per-test invocation |
| `commands.install` | `pip install -e '.[dev]'` | Editable install with dev dependencies |
| `commands.lint` | (not configured) | No linter currently configured |
| `commands.typecheck` | (not configured) | No typechecker currently configured |
| `commands.build_image_dev_sandbox` | `scripts/setup-dev-sandbox.sh` | Manual dev-sandbox image |
| `commands.build_image_hermes` | `scripts/build-hermes-image.sh` | Agentic sandbox image |

### Canonical documentation roots

| Doc | Relative path | Role |
|---|---|---|
| README | `README.md` | Repo overview, install, basic usage, status table |
| Architecture | `SANDBOX-ARCHITECTURE.md` | High-level workflows, container internals, threat model |
| Detailed flow | `SANDBOX-DETAILED-FLOW.md` | Phase-by-phase execution with exact bash |
| Implementation plan | `IMPLEMENTATION-PLAN.md` | Build roadmap, component breakdown, phase status |
| Agent config | `SANDBOX-AGENT-CONFIG.md` + `.html` | In-sandbox agent permission profile |
| Image toolchain | `SANDBOX-IMAGE-TOOLCHAIN.md` | Dockerfile design |
| Orchestration | `SANDBOX-ORCHESTRATION.md` + `.html` | MCP/OAuth IPC channel design |
| Flows (rendered) | `SANDBOX-FLOWS.html` | End-to-end mermaid flows |
| Control plane | `SANDBOX-CONTROL-PLANE.html` | Agentic control plane design (status/events/inbox/outbox + Hermes) |
| Session handoff | `SESSION-HANDOFF.md` | Resume-from-here state |
| Usage | `USAGE.md` | End-user CLI usage |
| Troubleshooting | `TROUBLESHOOTING.md` | Common failure recovery |
| Agent instructions | `AGENT-INSTRUCTIONS.md` | Phased delivery guide for autonomous agent implementation |
| Changelog | `CHANGELOG.md` | Version history |
| Verification | `docs/PHASE-5-PREREQS-VERIFICATION.md` | Phase 5 readiness check |

### Read exclusions (must not be visible to a sandboxed agent operating against this repo)

These paths must be excluded from any agent's read scope when this repo is mounted into an agentic sandbox:

| Pattern | Reason |
|---|---|
| `.env*` | Environment files possibly containing credentials |
| `venv/` | Local Python virtual environment, vendored deps |
| `.venv/` | Alternate virtualenv name |
| `lcm_sandbox.egg-info/` | Build metadata |
| `__pycache__/` (recursive) | Compiled Python bytecode |
| `.pytest_cache/` | Test runner cache |
| `*.egg-info/` | Build metadata |
| `build/`, `dist/` | Build artifacts |
| `.uv.lock`, `uv.lock` | Lockfile (read OK; exclude from sparse checkout only if not needed) |
| `node_modules/` | (not present today, but excluded by convention) |

### Vendored / build-artifact patterns (informational; exclude from topology-driven mounts)

Same as read exclusions above plus: `lcm_sandbox/__pycache__/`, `lcm_sandbox/tests/__pycache__/`, any `.coverage*` files.

### Database

LCM-Sandbox has **no database** — runs as a CLI against Docker + git. Skip database scaffolding when applying standard structure.

```yaml
database: none
```

### Write coordination

| Setting | Value | Role |
|---|---|---|
| `coordination.enabled` | `true` | Reservation required |
| `coordination.scope` | `shared_paths_only` | Default per global standard; reserve before editing shared/canonical files |
| `coordination.mechanism` | `queuectl` | Authoritative reservation tool |
| `coordination.queue_file` | `/Users/liborballaty/.ai-dev-dotfiles/.codex/memories/AGENT-WORK-QUEUE.md` | Shared queue backing store |

LCM-Sandbox does not have a repo-local `AGENTS.md` declaring `all_writes`, so the global default `shared_paths_only` applies.

---

## Minimal Generic Mapping For Global Tools

```yaml
repo_root:        .
project_root:     .
code_root:        lcm_sandbox
tests_root:       lcm_sandbox/tests
scripts_root:     scripts
repo_docs_root:   .
tracker_root:     null   # uses aidevops/design/TODO.md

stack:            python_service_cli
language:         python
package_manager:  uv
manifest:         pyproject.toml

commands:
  test:    pytest
  install: pip install -e '.[dev]'

database: none

coordination:
  enabled:  true
  scope:    shared_paths_only
  mechanism: queuectl
  queue_file: /Users/liborballaty/.ai-dev-dotfiles/.codex/memories/AGENT-WORK-QUEUE.md

standard_version: pre-2026.06.19   # authored before scaffold standard (#120) was formalized
```

---

## Why This Repo Needs A Topology Contract

LCM-Sandbox is the **sandbox engine** that the agentic sandbox design (`aidevops/design/TODO.md` #115) depends on. The agentic sandbox needs to know, for any target repo, what to mount and what to exclude. This file is the worked example for what every sandbox-target repo will eventually declare.

For LCM-Sandbox itself, agents operating on the repo need to:
- find the Python package without grep (`lcm_sandbox/`)
- find tests without grep (`lcm_sandbox/tests/`)
- find the bash dev-sandbox flow scripts (`scripts/`)
- not be confused by `venv/` or `lcm_sandbox.egg-info/`
- know that there is no database in this repo

---

## Update Rule

Update this file in the same work cycle when:
- a new top-level directory is added or removed,
- the package layout changes (e.g., adding a new sub-package outside `lcm_sandbox/`),
- a new test runner, linter, or typechecker is configured,
- a new entry-point script is added to `pyproject.toml`,
- a database is introduced.

Agents should prefer reading this file before making assumptions about where things live.
