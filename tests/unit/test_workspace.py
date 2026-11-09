from pathlib import Path

import pytest

from racegreen.workspace.manager import UnsafeWorkspaceError, WorkspaceManager


def test_manifest_rejects_branches_outside_the_run(tmp_path: Path) -> None:
    payload = {
        "run_id": "run-1",
        "workspaces": [
            {
                "agent_id": "agent-1",
                "path": str(tmp_path / "worktrees" / "agent-1"),
                "branch": "main",
                "base_commit": "abc",
            }
        ],
    }

    with pytest.raises(UnsafeWorkspaceError, match="not owned by this run"):
        WorkspaceManager._decode_workspaces(payload, expected_root=tmp_path)


def test_dirty_repository_requires_explicit_opt_in(tmp_path: Path) -> None:
    pytest.importorskip("subprocess").run(["git", "init", str(tmp_path)], check=True)
    (tmp_path / "README").write_text("initial\n", encoding="utf-8")
    pytest.importorskip("subprocess").run(["git", "-C", str(tmp_path), "add", "README"], check=True)
    pytest.importorskip("subprocess").run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "initial",
        ],
        check=True,
    )
    (tmp_path / "README").write_text("changed\n", encoding="utf-8")

    manager = WorkspaceManager(tmp_path, tmp_path.parent / "runs")
    with pytest.raises(UnsafeWorkspaceError, match="uncommitted"):
        manager.create_run()


def test_worktree_lifecycle_preserves_source_files(tmp_path: Path) -> None:
    import subprocess

    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
    (source / "README").write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "add", "README"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "initial",
        ],
        check=True,
        capture_output=True,
    )
    untracked = source / "keep.txt"
    untracked.write_text("keep\n", encoding="utf-8")

    manager = WorkspaceManager(source, tmp_path / "runs")
    layout = manager.create_run(allow_dirty=True)
    workspace = manager.create_agent_workspace(layout, "agent-1")

    assert workspace.path.is_dir()
    assert (workspace.path / "README").read_text(encoding="utf-8") == "initial\n"
    assert untracked.read_text(encoding="utf-8") == "keep\n"
    assert manager.cleanup(layout) == ()
    assert source.exists()
    assert untracked.exists()
