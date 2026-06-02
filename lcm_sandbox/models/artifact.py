"""Result/baseline models for sandbox runs."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class WorktreeBaseline(BaseModel):
    """Snapshot of the worktree state immediately after sync, before launch."""

    sandbox_id: str
    worktree_path: Path
    branch_name: str
    latest_commit: str
    latest_commit_msg: str
    sync_timestamp: datetime
    disk_size_before: str | None = None


class Phase1Result(BaseModel):
    """Result returned by `lcm-sandbox create` after Phases 0-3 succeed."""

    sandbox_id: str
    status: Literal["ready_for_docker_launch", "completed", "error"]
    worktree_path: Path
    repo_path: Path
    branch: str
    latest_commit: str
    phase: int = Field(ge=0, le=6)
    next_step: str
    image_id: str | None = None
    baseline: WorktreeBaseline | None = None
