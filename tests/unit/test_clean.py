import json

from typer.testing import CliRunner

from racegreen.cli import app


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
