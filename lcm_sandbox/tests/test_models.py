"""Tests for Pydantic models."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from lcm_sandbox.models import AllowedPaths, SandboxConfig


class TestAllowedPaths:
    def test_defaults(self) -> None:
        ap = AllowedPaths()
        assert ap.write == []
        assert ap.read == ["*"]

    def test_relative_paths_ok(self) -> None:
        ap = AllowedPaths(write=["src/", "tests/"], read=["*"])
        assert ap.write == ["src/", "tests/"]

    def test_absolute_path_rejected(self) -> None:
        with pytest.raises(ValidationError, match="repo-relative"):
            AllowedPaths(write=["/etc/passwd"])

    def test_traversal_rejected(self) -> None:
        with pytest.raises(ValidationError, match="traverse upward"):
            AllowedPaths(write=["../etc"])


class TestSandboxConfig:
    def _kwargs(self, **overrides: object) -> dict:
        base = dict(
            plan_id="plan_abc",
            run_id="run_xyz",
            repo_path=Path("/tmp/repo"),
            branch_name="feature/x",
            allowed_paths=AllowedPaths(write=["src/"]),
            timeout_minutes=60,
        )
        base.update(overrides)
        return base

    def test_valid_config(self) -> None:
        cfg = SandboxConfig(**self._kwargs())
        assert cfg.plan_id == "plan_abc"
        assert cfg.colima_profile == "LCM-Dev"

    def test_relative_repo_path_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must be absolute"):
            SandboxConfig(**self._kwargs(repo_path=Path("./repo")))

    def test_timeout_below_minimum(self) -> None:
        with pytest.raises(ValidationError):
            SandboxConfig(**self._kwargs(timeout_minutes=10))

    def test_timeout_above_maximum(self) -> None:
        with pytest.raises(ValidationError):
            SandboxConfig(**self._kwargs(timeout_minutes=500))

    def test_unsafe_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must match"):
            SandboxConfig(**self._kwargs(plan_id="bad plan!"))

    def test_branch_with_whitespace_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace"):
            SandboxConfig(**self._kwargs(branch_name="feature x"))
