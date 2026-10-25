"""Run-scoped creation, ownership tracking, verification, and cleanup."""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .git import GitError, add_worktree, inspect_repository, remove_worktree
from .models import AgentWorkspace, RunLayout


class UnsafeWorkspaceError(RuntimeError):
    """Raised when setup could risk user-owned data."""


class WorkspaceManager:
    def __init__(self, repository: Path, artifacts_root: Path | None = None) -> None:
        self.repository = inspect_repository(repository)
        default_root = Path.home() / ".racegreen" / "runs"
        self.artifacts_root = (artifacts_root or default_root).expanduser().resolve()
        if (
            self.artifacts_root == self.repository.root
            or self.repository.root in self.artifacts_root.parents
        ):
            raise UnsafeWorkspaceError(
                "Run artifacts must not be stored inside the source repository"
            )

    def create_run(self, *, allow_dirty: bool = False) -> RunLayout:
        if self.repository.is_dirty and not allow_dirty:
            raise UnsafeWorkspaceError(
                "Source repository has uncommitted changes; pass allow_dirty=True after reviewing them"
            )
        run_id = uuid.uuid4().hex
        root = self.artifacts_root / run_id
        root.mkdir(parents=True, exist_ok=False)
        artifacts = root / "artifacts"
        worktrees = root / "worktrees"
        artifacts.mkdir()
        worktrees.mkdir()
        layout = RunLayout(run_id, root, artifacts, worktrees, self.repository)
        self._write_manifest(layout, ())
        return layout

    def create_agent_workspace(self, layout: RunLayout, agent_id: str) -> AgentWorkspace:
        self._validate_layout(layout)
        if not agent_id or Path(agent_id).name != agent_id:
            raise UnsafeWorkspaceError("Agent IDs must be non-empty single path components")
        if any(item.agent_id == agent_id for item in self._read_workspaces(layout)):
            raise UnsafeWorkspaceError(f"Agent workspace already exists: {agent_id}")
        path = layout.worktrees / agent_id
        branch = f"racegreen/{layout.run_id}/{agent_id}"
        add_worktree(self.repository.root, path, layout.base.head, branch)
        workspace = AgentWorkspace(layout.run_id, agent_id, path, branch, layout.base.head)
        current = self._read_workspaces(layout)
        self._write_manifest(layout, (*current, workspace))
        return workspace

    def cleanup_agent_workspace(self, layout: RunLayout, workspace: AgentWorkspace) -> None:
        try:
            remove_worktree(self.repository.root, workspace)
        except GitError:
            pass
        # update manifest
        current = [w for w in self._read_workspaces(layout) if w.agent_id != workspace.agent_id]
        self._write_manifest(layout, tuple(current))

    def recover_run(self, run_root: Path) -> tuple[AgentWorkspace, ...]:
        """Read ownership metadata after interruption without guessing what is safe to delete."""
        root = run_root.expanduser().resolve(strict=True)
        manifest = root / "ownership.json"
        if not manifest.is_file():
            raise UnsafeWorkspaceError(f"Not a RaceGreen run directory: {root}")
        return self._decode_workspaces(
            json.loads(manifest.read_text(encoding="utf-8")), expected_root=root
        )

    def cleanup(self, layout: RunLayout) -> tuple[str, ...]:
        """Clean only owned, clean worktrees; retain evidence when cleanup is incomplete."""
        self._validate_layout(layout)
        errors: list[str] = []
        workspaces = self._read_workspaces(layout)
        for workspace in workspaces:
            try:
                remove_worktree(self.repository.root, workspace)
            except GitError as exc:
                errors.append(f"{workspace.agent_id}: {exc}")
        if errors:
            return tuple(errors)
        marker = layout.root / "ownership.json"
        if not marker.is_file():
            raise UnsafeWorkspaceError(f"Ownership marker missing: {layout.root}")
        shutil.rmtree(layout.root)
        return ()

    def _write_manifest(self, layout: RunLayout, workspaces: tuple[AgentWorkspace, ...]) -> None:
        payload = {
            "schema_version": 1,
            "run_id": layout.run_id,
            "repository": str(layout.base.root),
            "base_commit": layout.base.head,
            "created_at": datetime.now(UTC).isoformat(),
            "workspaces": [
                {
                    "agent_id": item.agent_id,
                    "path": str(item.path),
                    "branch": item.branch,
                    "base_commit": item.base_commit,
                }
                for item in workspaces
            ],
        }
        (layout.root / "ownership.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )

    def _validate_layout(self, layout: RunLayout) -> None:
        run_root = layout.root.resolve()
        if run_root.parent != self.artifacts_root or not (run_root / "ownership.json").is_file():
            raise UnsafeWorkspaceError(
                f"Refusing operation outside an owned run directory: {run_root}"
            )
        if layout.worktrees.resolve().parent != run_root:
            raise UnsafeWorkspaceError(
                f"Worktree directory escapes the owned run directory: {layout.worktrees}"
            )

    def _read_workspaces(self, layout: RunLayout) -> tuple[AgentWorkspace, ...]:
        return self._decode_workspaces(
            json.loads((layout.root / "ownership.json").read_text()), expected_root=layout.root
        )

    @staticmethod
    def _decode_workspaces(
        payload: object, *, expected_root: Path | None = None
    ) -> tuple[AgentWorkspace, ...]:
        if not isinstance(payload, dict) or not isinstance(payload.get("workspaces"), list):
            raise UnsafeWorkspaceError("Malformed RaceGreen ownership manifest")
        result: list[AgentWorkspace] = []
        for item in payload["workspaces"]:
            if not isinstance(item, dict):
                raise UnsafeWorkspaceError("Malformed workspace ownership entry")
            path = Path(str(item["path"])).resolve()
            if expected_root is not None:
                worktree_root = (expected_root / "worktrees").resolve()
                if path.parent != worktree_root:
                    raise UnsafeWorkspaceError(f"Workspace escapes the owned run directory: {path}")
            result.append(
                AgentWorkspace(
                    str(payload["run_id"]),
                    str(item["agent_id"]),
                    path,
                    str(item["branch"]),
                    str(item["base_commit"]),
                )
            )
        return tuple(result)
