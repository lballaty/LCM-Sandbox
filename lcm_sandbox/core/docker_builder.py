"""Phase 3: docker image preparation.

Implements STEP 3.1 (check image exists; build if not) and STEP 3.2 (verify
functional).

Phase 1 of the overall delivery does NOT yet ship a Dockerfile — the
Dockerfile arrives in Phase 2 of AGENT-INSTRUCTIONS.md. Therefore this module
*only checks* by default. If `build_if_missing=True` is passed and a
Dockerfile path is supplied, it will build.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lcm_sandbox.exceptions import DockerImageError
from lcm_sandbox.utils import docker as docker_utils
from lcm_sandbox.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_IMAGE_TAG = "lcm-dev-agent:latest"


@dataclass(slots=True)
class DockerImageInfo:
    tag: str
    image_id: str
    built: bool


def prepare_image(
    tag: str = DEFAULT_IMAGE_TAG,
    *,
    build_if_missing: bool = False,
    dockerfile: Path | None = None,
    context_path: Path | None = None,
    verify_functional: bool = True,
) -> DockerImageInfo:
    """Ensure the agent image exists locally; optionally build and verify."""
    image_id = docker_utils.image_id(tag)
    built = False

    if image_id is None:
        if not build_if_missing:
            raise DockerImageError(
                "agent image is not present locally; pass --build to construct it",
                step="3.1",
                tag=tag,
            )
        if dockerfile is None or context_path is None:
            raise DockerImageError(
                "cannot build image without dockerfile + context_path",
                step="3.1",
                tag=tag,
            )
        if not dockerfile.exists():
            raise DockerImageError(
                "dockerfile not found",
                step="3.1",
                dockerfile=str(dockerfile),
            )
        logger.info(
            "image_build_started",
            extra={"phase": 3, "step": "3.1", "tag": tag, "dockerfile": str(dockerfile)},
        )
        result = docker_utils.image_build(tag, context_path, dockerfile=dockerfile)
        if not result.ok:
            raise DockerImageError(
                "docker build failed",
                step="3.1",
                tag=tag,
                stderr=result.stderr.strip(),
            )
        image_id = docker_utils.image_id(tag)
        built = True

    if image_id is None:
        raise DockerImageError(
            "image still not present after build attempt",
            step="3.1",
            tag=tag,
        )

    if verify_functional:
        _verify_functional(tag)

    logger.info(
        "image_ready",
        extra={
            "phase": 3,
            "step": "3.2",
            "tag": tag,
            "image_id": image_id,
            "built": built,
        },
    )
    return DockerImageInfo(tag=tag, image_id=image_id, built=built)


def _verify_functional(tag: str) -> None:
    # STEP 3.2: run a sanity command. We check `git --version` only at this
    # phase; claude/codex availability is verified once the Dockerfile lands
    # in Phase 2.
    result = docker_utils.run_check(tag, ["/bin/sh", "-c", "git --version"])
    if not result.ok:
        raise DockerImageError(
            "image failed functional verification (git --version)",
            step="3.2",
            tag=tag,
            stderr=result.stderr.strip(),
            stdout=result.stdout.strip(),
        )
