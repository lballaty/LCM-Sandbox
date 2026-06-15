# File: lcm_sandbox/persona/__init__.py
# Description: Persona state renderer + capturer for HERMES-PERSONA-INTEGRATION-PLAN WP-8.
#              Materializes persona-owned files (config.yaml, SOUL.md, MEMORY.md, .env, skills/)
#              from the AIDevOps platform DB + persona repo, and detects post-run mutations to
#              emit proposed_persona_changes entries.
# Author: Libor Ballaty <libor@arionetworks.com>
# Created: 2026-06-12

from lcm_sandbox.persona.renderer import render_persona, RenderConfig
from lcm_sandbox.persona.capturer import capture_persona_mutations, CaptureConfig

__all__ = [
    "render_persona",
    "RenderConfig",
    "capture_persona_mutations",
    "CaptureConfig",
]
