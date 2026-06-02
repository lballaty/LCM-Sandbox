"""Subprocess wrappers with structured logging and consistent error handling."""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    cwd: Path | None

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def __str__(self) -> str:
        return f"{shlex.join(self.args)} -> exit={self.returncode}"


def run(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
    check: bool = False,
) -> CommandResult:
    """Run a command and capture stdout/stderr.

    Never invokes a shell; `args` must be a list. If `check=True` and the
    command exits non-zero, raises CalledProcessError.
    """
    proc = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        check=False,
    )
    result = CommandResult(
        args=list(args),
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        cwd=cwd,
    )
    if check and not result.ok:
        raise subprocess.CalledProcessError(
            result.returncode, args, output=result.stdout, stderr=result.stderr
        )
    return result
