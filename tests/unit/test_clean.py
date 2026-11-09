import json
import subprocess

from typer.testing import CliRunner

from racegreen.cli import app
from racegreen.workspace.manager import WorkspaceManager


def test_clean_only_removes_owned_run_directories(tmp_path):
    owned = tmp_path / "run-1"
    owned.mkdir()
    (owned / "ownership.json").write_text("{}", encoding="utf-8")
    unrelated = tmp_path / "notes"
    unrelated.mkdir()

    runner = CliRunner()
    listed = runner.invoke(app, ["clean", "--artifacts-root", str(tmp_path)])
    assert json.loads(listed.stdout)["runs"] == ["run-1"]
    removed = runner.invoke(app, ["clean", "--artifacts-root", str(tmp_path), "--yes"])
    assert removed.exit_code == 0
    assert not owned.exists()
    assert unrelated.exists()


def test_clean_removes_owned_worktree_and_branch(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "README").write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "init", str(repository)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repository), "add", "README"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
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
    runs = tmp_path / "runs"
    manager = WorkspaceManager(repository, runs)
    layout = manager.create_run()
    workspace = manager.create_agent_workspace(layout, "agent-1")

    result = CliRunner().invoke(
        app, ["clean", "--artifacts-root", str(runs), "--yes"]
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["removed"] == [layout.run_id]
    assert not workspace.path.exists()
    branches = subprocess.run(
        ["git", "-C", str(repository), "branch", "--list", workspace.branch],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert branches.strip() == ""
