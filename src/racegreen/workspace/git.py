from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .models import RepositoryState

if TYPE_CHECKING:
    from .models import AgentWorkspace


class GitError(RuntimeError):
    """Raised when a Git operation fails or returns unexpected data."""


def _run_git(repository: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repository,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except OSError as exc:
        raise GitError(f"Unable to execute Git in {repository}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"Git command timed out in {repository}: {' '.join(args)}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise GitError(f"Git command failed ({result.returncode}): {' '.join(args)}: {detail}")
    return result.stdout


def inspect_repository(path: Path) -> RepositoryState:
    """Capture an immutable snapshot of the repository's starting state."""
    candidate = path.expanduser().resolve(strict=True)
    if not candidate.is_dir():
        raise GitError(f"Repository path is not a directory: {candidate}")

    root = Path(_run_git(candidate, "rev-parse", "--show-toplevel").strip()).resolve(strict=True)
    head = _run_git(root, "rev-parse", "HEAD").strip()
    branch_name = _run_git(root, "branch", "--show-current").strip()
    status = _run_git(root, "status", "--porcelain=v1", "-z")
    dirty_paths = tuple(
        entry[3:] if len(entry) >= 3 else entry for entry in status.split("\0") if entry
    )
    submodules = tuple(line for line in _run_git(root, "submodule", "status").splitlines() if line)
    git_version = _run_git(root, "--version").strip()
    return RepositoryState(
        root=root,
        head=head,
        branch=branch_name or None,
        dirty_paths=dirty_paths,
        submodules=submodules,
        git_version=git_version,
        captured_at=datetime.now(UTC),
    )


def verify_worktree(path: Path, expected_commit: str) -> None:
    """Verify that a created worktree resolves to the expected repository and commit."""
    actual_root = Path(_run_git(path, "rev-parse", "--show-toplevel").strip()).resolve(strict=True)
    actual_commit = _run_git(path, "rev-parse", "HEAD").strip()
    if actual_root != path.resolve(strict=True):
        raise GitError(f"Worktree root mismatch: expected {path}, got {actual_root}")
    if actual_commit != expected_commit:
        raise GitError(
            f"Worktree base mismatch at {path}: expected {expected_commit}, got {actual_commit}"
        )


def add_worktree(repository: Path, path: Path, base_commit: str, branch: str) -> None:
    """Create a non-forced, named worktree from an already captured commit."""
    if path.exists():
        raise GitError(f"Refusing to overwrite existing worktree path: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    _run_git(repository, "worktree", "add", "-b", branch, str(path), base_commit)
    verify_worktree(path, base_commit)


def remove_worktree(repository: Path, workspace: AgentWorkspace) -> None:
    """Remove only a known worktree, without forcing away uncommitted changes."""
    _run_git(repository, "worktree", "remove", str(workspace.path))
    _run_git(repository, "branch", "-d", workspace.branch)
