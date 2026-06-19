"""
File: lcm_sandbox/cli_emit.py
Description:
    `sandbox-emit` CLI — lets the in-sandbox agent emit status updates,
    events, and outbox messages from its tool calls. This is the Phase A
    bridge used until Hermes is in the image (#112); once Hermes lands,
    Hermes calls control_plane.py directly and the agent talks to Hermes
    over HTTP instead.

    The CLI resolves the control directory from the CONTROL_DIR env var
    (set by the container entrypoint to `/control`). All subcommands write
    via control_plane writers so file integrity (atomic writes, monotonic
    seq) is consistent regardless of how the writers are invoked.

Usage examples:
    sandbox-emit status --phase executing --step "Refactor parser" --step-index 3 --step-total 7
    sandbox-emit event step_started --payload-json '{"step":1,"title":"Read code"}'
    sandbox-emit event commit --payload-json '{"sha":"a1b2c3d","message":"refactor parser"}'
    sandbox-emit heartbeat
    sandbox-emit ask --question "Adopt strict mode?" --options "yes,no" --timeout 1800

Author: Libor Ballaty <libor@arionetworks.com>
Created: 2026-06-19
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from lcm_sandbox.core.control_plane import (
    ControlPaths,
    EventLogger,
    Heartbeat,
    InboxWatcher,
    OutboxWriter,
    StatusWriter,
    load_plan,
)


def _resolve_paths() -> ControlPaths:
    control_dir = os.environ.get("CONTROL_DIR", "/control")
    return ControlPaths(root=Path(control_dir))


def _run_id_from_plan(paths: ControlPaths) -> str:
    try:
        plan = load_plan(paths)
        return plan.get("run_id", "unknown")
    except (FileNotFoundError, json.JSONDecodeError):
        return os.environ.get("RUN_ID", "unknown")


def cmd_status(args: argparse.Namespace) -> int:
    paths = _resolve_paths()
    paths.ensure_layout()
    writer = StatusWriter(paths, run_id=_run_id_from_plan(paths))

    update_fields: dict = {}
    if args.phase is not None:
        update_fields["phase"] = args.phase
    if args.step is not None:
        update_fields["current_step"] = args.step
    if args.step_index is not None:
        update_fields["step_index"] = args.step_index
    if args.step_total is not None:
        update_fields["step_total"] = args.step_total
    if args.exit_code is not None:
        update_fields["exit_code"] = args.exit_code
    if args.blocked_on is not None:
        update_fields["blocked_on"] = args.blocked_on if args.blocked_on != "null" else None

    new_state = writer.update(**update_fields) if update_fields else writer.snapshot()
    print(json.dumps(new_state, indent=2))
    return 0


def cmd_event(args: argparse.Namespace) -> int:
    paths = _resolve_paths()
    paths.ensure_layout()
    logger = EventLogger(paths)

    payload: dict = {}
    if args.payload_json:
        try:
            payload = json.loads(args.payload_json)
        except json.JSONDecodeError as e:
            print(f"error: --payload-json invalid: {e}", file=sys.stderr)
            return 2

    seq = logger.append(args.type, payload)
    print(json.dumps({"seq": seq, "type": args.type}))
    return 0


def cmd_heartbeat(args: argparse.Namespace) -> int:
    paths = _resolve_paths()
    paths.ensure_layout()
    writer = StatusWriter(paths, run_id=_run_id_from_plan(paths))
    writer.heartbeat()
    return 0


def cmd_commit(args: argparse.Namespace) -> int:
    """Convenience: emit a commit event AND update status manifest."""
    paths = _resolve_paths()
    paths.ensure_layout()
    writer = StatusWriter(paths, run_id=_run_id_from_plan(paths))
    logger = EventLogger(paths)
    writer.add_commit(args.sha)
    logger.append("commit", {"sha": args.sha, "message": args.message or ""})
    return 0


def cmd_file_changed(args: argparse.Namespace) -> int:
    paths = _resolve_paths()
    paths.ensure_layout()
    writer = StatusWriter(paths, run_id=_run_id_from_plan(paths))
    writer.add_file_changed(args.path)
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    """Blocking ask: write to outbox, wait for inbox response.

    Exit code 0 with stdout = the answer value on success.
    Exit code 1 on timeout.
    """
    paths = _resolve_paths()
    paths.ensure_layout()
    outbox = OutboxWriter(paths)
    inbox = InboxWatcher(paths)
    logger = EventLogger(paths)
    writer = StatusWriter(paths, run_id=_run_id_from_plan(paths))

    msg_id = outbox.next_msg_id()
    options = [o.strip() for o in args.options.split(",")] if args.options else []
    outbox.write(
        msg_id=msg_id,
        type_="ask",
        question=args.question,
        options=options,
        context=args.context or "",
    )
    writer.update(phase="blocked", blocked_on=msg_id)
    logger.append("blocked", {"msg_id": msg_id})

    response = inbox.wait_for(msg_id, timeout_seconds=args.timeout)
    if response is None:
        logger.append("failed", {"reason": "ask_timeout", "msg_id": msg_id})
        writer.update(phase="failed", exit_code=124)
        print(f"timeout waiting for response to {msg_id}", file=sys.stderr)
        return 1

    answer = response.get("value", "")
    logger.append("unblocked", {"msg_id": msg_id, "answer": answer})
    writer.update(phase="executing", blocked_on=None)
    print(answer)
    return 0


def cmd_tasks_complete(args: argparse.Namespace) -> int:
    paths = _resolve_paths()
    paths.ensure_layout()
    writer = StatusWriter(paths, run_id=_run_id_from_plan(paths))
    logger = EventLogger(paths)
    met = [m.strip() for m in args.criteria_met.split(",")] if args.criteria_met else []
    logger.append("tasks_complete", {"exit_criteria_met": met})
    writer.update(phase="complete", exit_code=0)
    return 0


def cmd_failed(args: argparse.Namespace) -> int:
    paths = _resolve_paths()
    paths.ensure_layout()
    writer = StatusWriter(paths, run_id=_run_id_from_plan(paths))
    logger = EventLogger(paths)
    logger.append("failed", {
        "reason": args.reason,
        "error": args.error or "",
    })
    writer.update(phase="failed", exit_code=args.exit_code or 1)
    return 0


def cmd_plan_loaded(args: argparse.Namespace) -> int:
    paths = _resolve_paths()
    paths.ensure_layout()
    logger = EventLogger(paths)
    plan = load_plan(paths)
    logger.append("plan_loaded", {"plan_id": plan.get("plan_id")})
    print(json.dumps(plan, indent=2))
    return 0


def cmd_heartbeat_daemon(args: argparse.Namespace) -> int:
    """Run the heartbeat in foreground until killed.

    Useful for entrypoint scripts that want a long-running heartbeat
    without the agent having to call sandbox-emit heartbeat repeatedly.
    """
    paths = _resolve_paths()
    paths.ensure_layout()
    writer = StatusWriter(paths, run_id=_run_id_from_plan(paths))
    hb = Heartbeat(writer, interval_seconds=args.interval)
    hb.start()
    try:
        # Block forever; SIGTERM/SIGINT exits cleanly
        while True:
            import time as _t
            _t.sleep(3600)
    except KeyboardInterrupt:
        hb.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sandbox-emit",
        description=(
            "Emit control-plane status, events, and outbox messages from "
            "inside an agentic sandbox container. Phase A bridge until "
            "Hermes is in-image (#112)."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # status
    p_status = sub.add_parser("status", help="Update or read status.json")
    p_status.add_argument("--phase", choices=["starting", "executing", "blocked",
                                              "complete", "failed", "stalled"])
    p_status.add_argument("--step")
    p_status.add_argument("--step-index", type=int)
    p_status.add_argument("--step-total", type=int)
    p_status.add_argument("--exit-code", type=int)
    p_status.add_argument("--blocked-on", help="msg_id when phase=blocked; pass 'null' to clear")
    p_status.set_defaults(func=cmd_status)

    # event
    p_event = sub.add_parser("event", help="Append an event to events.jsonl")
    p_event.add_argument("type", help="Event type (see SANDBOX-CONTROL-SCHEMA.md)")
    p_event.add_argument("--payload-json", default="", help="JSON object for payload")
    p_event.set_defaults(func=cmd_event)

    # heartbeat (one-shot)
    p_hb = sub.add_parser("heartbeat", help="Update heartbeat timestamp once")
    p_hb.set_defaults(func=cmd_heartbeat)

    # heartbeat-daemon (long-running)
    p_hbd = sub.add_parser("heartbeat-daemon", help="Run heartbeat in foreground")
    p_hbd.add_argument("--interval", type=float, default=10.0)
    p_hbd.set_defaults(func=cmd_heartbeat_daemon)

    # commit
    p_commit = sub.add_parser("commit", help="Record a commit (updates status + emits event)")
    p_commit.add_argument("--sha", required=True)
    p_commit.add_argument("--message", default="")
    p_commit.set_defaults(func=cmd_commit)

    # file-changed
    p_fc = sub.add_parser("file-changed", help="Record a file change in status.manifest")
    p_fc.add_argument("--path", required=True)
    p_fc.set_defaults(func=cmd_file_changed)

    # ask (blocking)
    p_ask = sub.add_parser("ask", help="Ask operator and block for response")
    p_ask.add_argument("--question", required=True)
    p_ask.add_argument("--options", default="", help="comma-separated options")
    p_ask.add_argument("--context", default="")
    p_ask.add_argument("--timeout", type=float, default=1800.0)
    p_ask.set_defaults(func=cmd_ask)

    # tasks-complete
    p_tc = sub.add_parser("tasks-complete", help="Emit tasks_complete and mark complete")
    p_tc.add_argument("--criteria-met", default="", help="comma-separated criteria types met")
    p_tc.set_defaults(func=cmd_tasks_complete)

    # failed
    p_fail = sub.add_parser("failed", help="Emit failed event and mark failed")
    p_fail.add_argument("--reason", required=True)
    p_fail.add_argument("--error", default="")
    p_fail.add_argument("--exit-code", type=int, default=1)
    p_fail.set_defaults(func=cmd_failed)

    # plan-loaded
    p_pl = sub.add_parser("plan-loaded", help="Read plan.json and emit plan_loaded event")
    p_pl.set_defaults(func=cmd_plan_loaded)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
