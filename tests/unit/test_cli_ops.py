import json

from typer.testing import CliRunner

from racegreen.cli import app


def test_init_writes_no_credentials(tmp_path):
    config = tmp_path / "config.json"
    result = CliRunner().invoke(app, ["init", "--config", str(config)])
    assert result.exit_code == 0
    content = config.read_text(encoding="utf-8")
    assert "API_KEY" not in content
    assert json.loads(content)["network_policy"] == "provider-only"


def test_doctor_reports_checks(monkeypatch):
    monkeypatch.delenv("RACEGREEN_API_KEY", raising=False)
    monkeypatch.setenv("RACEGREEN_MODEL", "test-model")
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["checks"]["provider_configured"] is False
