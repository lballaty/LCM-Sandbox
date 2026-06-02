"""Pydantic models for sandbox configuration."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator

_SANDBOX_ID_SAFE = re.compile(r"^[A-Za-z0-9._-]+$")


class AllowedPaths(BaseModel):
    """Paths the sandboxed agent may write or read inside the worktree.

    Paths are repo-relative (e.g. "src/", "tests/"). "*" in `read` means
    "anywhere in the worktree".
    """

    write: list[str] = Field(default_factory=list)
    read: list[str] = Field(default_factory=lambda: ["*"])

    @field_validator("write", "read")
    @classmethod
    def _no_absolute_or_traversal(cls, v: list[str]) -> list[str]:
        for p in v:
            if p == "*":
                continue
            if p.startswith("/"):
                raise ValueError(f"allowed path must be repo-relative, got {p!r}")
            if ".." in Path(p).parts:
                raise ValueError(f"allowed path may not traverse upward: {p!r}")
        return v


class SandboxConfig(BaseModel):
    """Full configuration for a single sandbox run."""

    plan_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    repo_path: Path
    branch_name: str = Field(min_length=1)
    allowed_paths: AllowedPaths
    timeout_minutes: int = Field(ge=15, le=480)
    colima_profile: str = "LCM-Dev"

    @field_validator("repo_path")
    @classmethod
    def _repo_path_must_be_absolute(cls, v: Path) -> Path:
        if not v.is_absolute():
            raise ValueError(f"repo_path must be absolute, got {v}")
        return v

    @field_validator("plan_id", "run_id")
    @classmethod
    def _ids_safe(cls, v: str) -> str:
        if not _SANDBOX_ID_SAFE.match(v):
            raise ValueError(
                f"id must match {_SANDBOX_ID_SAFE.pattern}, got {v!r}"
            )
        return v

    @model_validator(mode="after")
    def _branch_no_whitespace(self) -> "SandboxConfig":
        if any(c.isspace() for c in self.branch_name):
            raise ValueError(f"branch_name may not contain whitespace: {self.branch_name!r}")
        return self
