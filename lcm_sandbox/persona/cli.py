# File: lcm_sandbox/persona/cli.py
# Description: Click CLI wrapping persona-state-renderer and persona-state-capturer.
#              Wired as console entry points in pyproject.toml.
# Author: Libor Ballaty <libor@arionetworks.com>
# Created: 2026-06-12

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import click

from lcm_sandbox.persona.capturer import (
    CaptureConfig,
    CaptureError,
    capture_persona_mutations,
)
from lcm_sandbox.persona.renderer import (
    RenderConfig,
    RenderError,
    render_persona,
)


def _default_api_key() -> str:
    return os.environ.get("AIDEVOPS_API_KEY", "dev-key")


@click.command("render")
@click.option("--persona-key", required=False, default=None, help="Persona key (e.g. config-auditor).")
@click.option("--persona-id", required=False, default=None, type=int, help="Persona id (alternative to --persona-key).")
@click.option("--output-dir", required=True, type=click.Path(file_okay=False),
              help="Host dir to render into (then docker-cp'd into HERMES_HOME).")
@click.option("--persona-repo-path", required=True, type=click.Path(exists=True, file_okay=False),
              help="Absolute path to the aidevops-hermes-personas repo working copy.")
@click.option("--platform-api-base", default="http://localhost:9700", show_default=True,
              help="Base URL of the AIDevOps platform REST API.")
@click.option("--platform-api-key", default=None,
              help="X-AIDevOps-Key value. Defaults to $AIDEVOPS_API_KEY or 'dev-key'.")
@click.option("--mcp-token", default="", help="Per-run MCP bearer token (substituted into config.yaml + .env).")
@click.option("--mcp-url", default="", help="MCP server URL (substituted into config.yaml).")
@click.option("--model-provider", default="", help="LLM provider key (substituted into config.yaml).")
@click.option("--model-key", default="", help="LLM model key (substituted into config.yaml).")
def render_cmd(
    persona_key: str | None,
    persona_id: int | None,
    output_dir: str,
    persona_repo_path: str,
    platform_api_base: str,
    platform_api_key: str | None,
    mcp_token: str,
    mcp_url: str,
    model_provider: str,
    model_key: str,
) -> None:
    """Render a persona's state into a host directory."""
    if not persona_key and not persona_id:
        click.echo(json.dumps({"status": "error", "message": "Provide --persona-key or --persona-id"}), err=True)
        sys.exit(2)

    cfg = RenderConfig(
        persona_key=persona_key or "",
        persona_id=persona_id,
        output_dir=Path(output_dir),
        persona_repo_path=Path(persona_repo_path),
        platform_api_base=platform_api_base,
        platform_api_key=platform_api_key or _default_api_key(),
        mcp_token=mcp_token,
        mcp_url=mcp_url,
        model_provider=model_provider,
        model_key=model_key,
    )
    try:
        result = render_persona(cfg)
    except RenderError as exc:
        click.echo(json.dumps(exc.to_json(), indent=2), err=True)
        sys.exit(1)

    click.echo(
        json.dumps(
            {
                "status": "ok",
                "persona_id": result.persona_id,
                "persona_key": result.persona_key,
                "output_dir": str(result.output_dir),
                "baseline_hash_file": str(result.baseline_hash_file) if result.baseline_hash_file else None,
                "rendered_files": result.rendered_files,
            },
            indent=2,
        )
    )
    sys.exit(0)


@click.command("capture")
@click.option("--persona-id", required=True, type=int)
@click.option("--scheduler-run-id", required=True, type=int)
@click.option("--post-run-dir", required=True, type=click.Path(exists=True, file_okay=False),
              help="Host dir containing the post-run HERMES_HOME snapshot (typically docker-cp output).")
@click.option("--baseline-hash-file", default=None, type=click.Path(),
              help="Baseline hashes JSON written by the renderer. Defaults to <post-run-dir>/.baseline-hashes.json.")
@click.option("--platform-api-base", default="http://localhost:9700", show_default=True)
@click.option("--platform-api-key", default=None,
              help="X-AIDevOps-Key value. Defaults to $AIDEVOPS_API_KEY or 'dev-key'.")
@click.option("--dry-run", is_flag=True, help="Print proposed changes; do not POST.")
def capture_cmd(
    persona_id: int,
    scheduler_run_id: int,
    post_run_dir: str,
    baseline_hash_file: str | None,
    platform_api_base: str,
    platform_api_key: str | None,
    dry_run: bool,
) -> None:
    """Diff post-run persona state against baseline and emit proposed_persona_changes."""
    cfg = CaptureConfig(
        persona_id=persona_id,
        scheduler_run_id=scheduler_run_id,
        post_run_dir=Path(post_run_dir),
        baseline_hash_file=Path(baseline_hash_file) if baseline_hash_file else None,
        platform_api_base=platform_api_base,
        platform_api_key=platform_api_key or _default_api_key(),
        dry_run=dry_run,
    )
    try:
        result = capture_persona_mutations(cfg)
    except CaptureError as exc:
        click.echo(json.dumps(exc.to_json(), indent=2), err=True)
        sys.exit(1)

    click.echo(
        json.dumps(
            {
                "status": "ok",
                "persona_id": result.persona_id,
                "scheduler_run_id": result.scheduler_run_id,
                "dry_run": dry_run,
                "proposed_changes": [
                    {"change_type": c.change_type, "payload": c.payload, "baseline_hash": c.baseline_hash}
                    for c in result.proposed_changes
                ],
                "posted_ids": result.posted_ids,
                "skipped_hermes_managed": result.skipped_hermes_managed,
                "notes": result.notes,
            },
            indent=2,
        )
    )
    sys.exit(0)


# A small group so `python3 -m lcm_sandbox.persona.cli render ...` works as a one-shot too.
@click.group()
def main() -> None:
    """Persona renderer/capturer (WP-8)."""


main.add_command(render_cmd)
main.add_command(capture_cmd)


if __name__ == "__main__":
    main()
