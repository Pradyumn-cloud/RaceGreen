import json

from typer.testing import CliRunner

from racegreen.cli import app


def test_demo_requires_no_provider(tmp_path):
    result = CliRunner().invoke(app, ["demo", "--artifacts-root", str(tmp_path)])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert (tmp_path / "report.html").is_file()
    assert payload["run_id"] == "demo-run"
