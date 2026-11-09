import json
import subprocess

from typer.testing import CliRunner

from racegreen.cli import app


def test_init_writes_no_credentials(tmp_path):
    config = tmp_path / "config.json"
    result = CliRunner().invoke(app, ["init", "--config", str(config)])
    assert result.exit_code == 0
    content = config.read_text(encoding="utf-8")
    assert "API_KEY" not in content
    assert json.loads(content)["network_policy"] == "provider-only"


def test_doctor_reports_checks(tmp_path, monkeypatch):
    monkeypatch.delenv("RACEGREEN_API_KEY", raising=False)
    monkeypatch.setenv("RACEGREEN_MODEL", "test-model")
    monkeypatch.setenv("RACEGREEN_CONFIG", str(tmp_path / "missing.json"))
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["checks"]["provider_configured"] is False


def _git(repository, *args):
    subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def test_race_dry_run_reports_state_without_starting_agents(tmp_path, monkeypatch):
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init")
    (repository / "file.txt").write_text("content\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(
        repository,
        "-c",
        "user.name=RaceGreen Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "base",
    )
    monkeypatch.setenv("RACEGREEN_CONFIG", str(tmp_path / "missing.json"))
    monkeypatch.setenv("RACEGREEN_MODEL", "test-model")

    result = CliRunner().invoke(
        app,
        [
            "race",
            str(repository),
            "task",
            "--dry-run",
            "--verification-command",
            "git --version",
            "--external-agent",
            "git --version",
            "--database",
            str(tmp_path / "racegreen.db"),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["ok"] is True
    assert payload["dirty"] is False
    assert payload["head"]
    assert payload["verification_command"] == ["git", "--version"]
    assert payload["external_agent_available"] is True
    assert not (tmp_path / "racegreen.db").exists()


def test_race_dry_run_rejects_missing_external_agent(tmp_path, monkeypatch):
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init")
    (repository / "file.txt").write_text("content\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(
        repository,
        "-c",
        "user.name=RaceGreen Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "base",
    )
    monkeypatch.setenv("RACEGREEN_CONFIG", str(tmp_path / "missing.json"))
    monkeypatch.setenv("RACEGREEN_MODEL", "test-model")

    result = CliRunner().invoke(
        app,
        [
            "race",
            str(repository),
            "task",
            "--dry-run",
            "--verification-command",
            "git --version",
            "--external-agent",
            "definitely-not-a-real-racegreen-command",
            "--database",
            str(tmp_path / "racegreen.db"),
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["external_agent_available"] is False


def test_resume_refuses_unknown_run(tmp_path):
    result = CliRunner().invoke(
        app,
        ["resume", "run-does-not-exist", "--database", str(tmp_path / "racegreen.db")],
    )

    assert result.exit_code == 2
    assert "unknown run" in result.output
