"""Tests for core.preflight (Phase 0 checks)."""

from __future__ import annotations

import pytest

from lcm_sandbox.core import preflight
from lcm_sandbox.exceptions import PreflightCheckError
from lcm_sandbox.tests.conftest import fail, ok


def _all_ok(mocker) -> None:
    """Stub every subprocess call to succeed by default."""
    mocker.patch.object(preflight.git_utils, "rev_parse_git_dir", return_value=ok(".git"))
    mocker.patch.object(preflight.git_utils, "show_ref", return_value=ok("abc123 refs/heads/x"))
    mocker.patch.object(preflight.docker_utils, "colima_status", return_value=ok("Running"))
    mocker.patch.object(preflight.docker_utils, "ps", return_value=ok())
    mocker.patch.object(preflight.docker_utils, "ps_filter", return_value=ok(""))


def test_all_checks_pass(mocker, sandbox_config) -> None:
    _all_ok(mocker)
    preflight.run_preflight(sandbox_config)  # no exception


def test_repo_missing(sandbox_config, tmp_path) -> None:
    bad = sandbox_config.model_copy(update={"repo_path": tmp_path / "does-not-exist"})
    with pytest.raises(PreflightCheckError, match="does not exist") as exc:
        preflight.run_preflight(bad)
    assert exc.value.context["check"] == 1


def test_repo_not_git(mocker, sandbox_config) -> None:
    _all_ok(mocker)
    mocker.patch.object(
        preflight.git_utils, "rev_parse_git_dir", return_value=fail("not a git repo")
    )
    with pytest.raises(PreflightCheckError, match="not a git repository") as exc:
        preflight.run_preflight(sandbox_config)
    assert exc.value.context["check"] == 1


def test_branch_missing(mocker, sandbox_config) -> None:
    _all_ok(mocker)
    mocker.patch.object(preflight.git_utils, "show_ref", return_value=fail())
    with pytest.raises(PreflightCheckError, match="branch not found") as exc:
        preflight.run_preflight(sandbox_config)
    assert exc.value.context["check"] == 2


def test_colima_not_running(mocker, sandbox_config) -> None:
    _all_ok(mocker)
    mocker.patch.object(
        preflight.docker_utils, "colima_status", return_value=ok("Stopped")
    )
    with pytest.raises(PreflightCheckError, match="not in Running state") as exc:
        preflight.run_preflight(sandbox_config)
    assert exc.value.context["check"] == 3


def test_colima_command_failed(mocker, sandbox_config) -> None:
    _all_ok(mocker)
    mocker.patch.object(
        preflight.docker_utils,
        "colima_status",
        return_value=fail("colima: command not found"),
    )
    with pytest.raises(PreflightCheckError, match="colima profile not running"):
        preflight.run_preflight(sandbox_config)


def test_docker_unreachable(mocker, sandbox_config) -> None:
    _all_ok(mocker)
    mocker.patch.object(preflight.docker_utils, "ps", return_value=fail("connection refused"))
    with pytest.raises(PreflightCheckError, match="docker daemon not accessible") as exc:
        preflight.run_preflight(sandbox_config)
    assert exc.value.context["check"] == 4


def test_duplicate_sandbox(mocker, sandbox_config) -> None:
    _all_ok(mocker)
    mocker.patch.object(
        preflight.docker_utils, "ps_filter", return_value=ok("abc123\n")
    )
    with pytest.raises(PreflightCheckError, match="already running") as exc:
        preflight.run_preflight(sandbox_config)
    assert exc.value.context["check"] == 8
