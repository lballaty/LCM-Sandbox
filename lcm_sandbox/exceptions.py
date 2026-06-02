"""Custom exceptions for lcm-sandbox.

Every exception carries the failing phase/step number and contextual fields
(repo_path, branch_name, etc.) so audit logs can render actionable errors.
"""

from __future__ import annotations

from typing import Any


class SandboxError(Exception):
    """Base class for all sandbox errors."""

    phase: int | None = None
    step: str | None = None

    def __init__(self, message: str, **context: Any) -> None:
        super().__init__(message)
        self.message = message
        self.context = context

    def __str__(self) -> str:
        if not self.context:
            return self.message
        ctx = " ".join(f"{k}={v!r}" for k, v in self.context.items())
        return f"{self.message} ({ctx})"


class PreflightCheckError(SandboxError):
    phase = 0
    step = "0.1"


class WorktreeError(SandboxError):
    phase = 1


class SyncError(SandboxError):
    phase = 2


class DockerImageError(SandboxError):
    phase = 3


class DockerLaunchError(SandboxError):
    phase = 4


class ArtifactCaptureError(SandboxError):
    phase = 6
