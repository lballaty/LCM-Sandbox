"""Tests for utils.shell + selected git/docker wrappers."""

from __future__ import annotations

from lcm_sandbox.utils import docker as docker_utils
from lcm_sandbox.utils import git as git_utils
from lcm_sandbox.utils.shell import run


def test_run_captures_stdout() -> None:
    result = run(["printf", "hello"])
    assert result.ok
    assert result.stdout == "hello"
    assert result.returncode == 0


def test_run_captures_stderr() -> None:
    result = run(["sh", "-c", "echo oops 1>&2; exit 2"])
    assert not result.ok
    assert result.returncode == 2
    assert "oops" in result.stderr


def test_command_result_str() -> None:
    result = run(["echo", "hi"])
    assert "echo" in str(result)
    assert "exit=0" in str(result)


def test_git_is_clean_uses_status_porcelain(mocker) -> None:
    from lcm_sandbox.utils.shell import CommandResult
    mock = mocker.patch(
        "lcm_sandbox.utils.git.run",
        return_value=CommandResult(
            args=["git", "status", "--porcelain"],
            returncode=0,
            stdout="",
            stderr="",
            cwd=None,
        ),
    )
    from pathlib import Path
    assert git_utils.is_clean(Path("/tmp/x")) is True
    mock.assert_called_once()


def test_docker_image_id_returns_none_on_missing(mocker) -> None:
    from lcm_sandbox.utils.shell import CommandResult
    mocker.patch(
        "lcm_sandbox.utils.docker.run",
        return_value=CommandResult(
            args=["docker", "image", "inspect"],
            returncode=1,
            stdout="",
            stderr="not found",
            cwd=None,
        ),
    )
    assert docker_utils.image_id("missing:tag") is None
