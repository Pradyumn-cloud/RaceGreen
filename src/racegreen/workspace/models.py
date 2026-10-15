"""Typed records for the Phase 1 workspace lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class RepositoryState:
    root: Path
    head: str
    branch: str | None
    dirty_paths: tuple[str, ...]
    submodules: tuple[str, ...]
    git_version: str
    captured_at: datetime

    @property
    def is_dirty(self) -> bool:
        return bool(self.dirty_paths)


@dataclass(frozen=True)
class RunLayout:
    run_id: str
    root: Path
    artifacts: Path
    worktrees: Path
    base: RepositoryState


@dataclass(frozen=True)
class AgentWorkspace:
    run_id: str
    agent_id: str
    path: Path
    branch: str
    base_commit: str
