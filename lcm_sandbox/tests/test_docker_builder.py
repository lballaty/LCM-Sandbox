"""Tests for core.docker_builder (Phase 3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lcm_sandbox.core import docker_builder
from lcm_sandbox.exceptions import DockerImageError
from lcm_sandbox.tests.conftest import fail, ok


def test_image_present_skips_build(mocker) -> None:
    mocker.patch.object(docker_builder.docker_utils, "image_id", return_value="sha256:abc")
    mocker.patch.object(
        docker_builder.docker_utils, "run_check", return_value=ok("git version 2.40")
    )
    info = docker_builder.prepare_image()
    assert info.built is False
    assert info.image_id == "sha256:abc"


def test_image_missing_without_build_flag_raises(mocker) -> None:
    mocker.patch.object(docker_builder.docker_utils, "image_id", return_value=None)
    with pytest.raises(DockerImageError, match="not present locally"):
        docker_builder.prepare_image(build_if_missing=False)


def test_build_requires_dockerfile(mocker) -> None:
    mocker.patch.object(docker_builder.docker_utils, "image_id", return_value=None)
    with pytest.raises(DockerImageError, match="dockerfile"):
        docker_builder.prepare_image(build_if_missing=True)


def test_build_success(mocker, tmp_path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM scratch\n")
    context = tmp_path

    image_ids = iter([None, "sha256:newly_built"])
    mocker.patch.object(
        docker_builder.docker_utils, "image_id", side_effect=lambda *a, **kw: next(image_ids)
    )
    mocker.patch.object(docker_builder.docker_utils, "image_build", return_value=ok())
    mocker.patch.object(docker_builder.docker_utils, "run_check", return_value=ok())

    info = docker_builder.prepare_image(
        build_if_missing=True, dockerfile=dockerfile, context_path=context
    )
    assert info.built is True
    assert info.image_id == "sha256:newly_built"


def test_build_failure_propagates(mocker, tmp_path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM scratch\n")

    mocker.patch.object(docker_builder.docker_utils, "image_id", return_value=None)
    mocker.patch.object(
        docker_builder.docker_utils, "image_build", return_value=fail("build err")
    )
    with pytest.raises(DockerImageError, match="docker build failed"):
        docker_builder.prepare_image(
            build_if_missing=True, dockerfile=dockerfile, context_path=tmp_path
        )


def test_functional_verification_failure(mocker) -> None:
    mocker.patch.object(docker_builder.docker_utils, "image_id", return_value="sha256:abc")
    mocker.patch.object(
        docker_builder.docker_utils, "run_check", return_value=fail("git: not found")
    )
    with pytest.raises(DockerImageError, match="functional verification"):
        docker_builder.prepare_image()
