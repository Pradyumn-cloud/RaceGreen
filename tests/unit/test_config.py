import json
from pathlib import Path

import pytest

from racegreen.agent.config import (
    ProviderConfig,
    default_config_path,
    is_loopback,
    load_repo_config,
    repo_config_path,
)
from racegreen.agent.external import _minimal_env


def test_repo_config_overrides_global_provider_settings(tmp_path: Path, monkeypatch) -> None:
    global_config = tmp_path / "global.json"
    global_config.write_text(
        json.dumps({"provider_url": "https://global.example", "model": "global-model"}),
        encoding="utf-8",
    )
    repository = tmp_path / "repo"
    repository.mkdir()
    repo_config = repo_config_path(repository)
    repo_config.parent.mkdir(parents=True)
    repo_config.write_text(
        json.dumps(
            {
                "provider_url": "http://127.0.0.1:11434",
                "model": "local-model",
                "requires_api_key": False,
                "agents": 1,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("RACEGREEN_CONFIG", str(global_config))
    monkeypatch.delenv("RACEGREEN_PROVIDER_URL", raising=False)
    monkeypatch.delenv("RACEGREEN_MODEL", raising=False)
    monkeypatch.delenv("RACEGREEN_API_KEY_ENV", raising=False)

    config = ProviderConfig.from_environment(repository=repository)

    assert config.endpoint == "http://127.0.0.1:11434"
    assert config.model == "local-model"
    assert config.requires_api_key is False
    assert config.repository_config_path == repo_config
    assert load_repo_config(repository)["agents"] == 1


def test_repo_config_rejects_embedded_credentials(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    path = repo_config_path(repository)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"model": "x", "api_key": "secret"}), encoding="utf-8")

    with pytest.raises(ValueError, match="must not contain credentials"):
        load_repo_config(repository)


def test_repo_config_rejects_nested_credentials(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    path = repo_config_path(repository)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"provider": {"headers": [{"authorization_token": "secret"}]}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="provider.headers\\[0\\].authorization_token"):
        load_repo_config(repository)


def test_repo_config_rejects_non_object_json(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    path = repo_config_path(repository)
    path.parent.mkdir(parents=True)
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(TypeError, match="must be a JSON object"):
        load_repo_config(repository)


def test_repo_config_validates_known_types(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    path = repo_config_path(repository)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"agents": "two"}), encoding="utf-8")

    with pytest.raises(TypeError, match="agents must be an integer"):
        load_repo_config(repository)


def test_default_config_path_honors_explicit_override(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "custom.json"
    monkeypatch.setenv("RACEGREEN_CONFIG", str(target))
    assert default_config_path() == target.resolve()


def test_loopback_detection_rejects_lookalike_domains() -> None:
    assert is_loopback("http://127.0.0.1:11434")
    assert is_loopback("http://localhost:11434")
    assert is_loopback("http://[::1]:11434")
    assert not is_loopback("https://127.0.0.1.example.com")
    assert not is_loopback("https://localhost.example.com")


def test_external_agent_environment_excludes_credentials_by_default(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("RACEGREEN_API_KEY", "secret")
    monkeypatch.delenv("RACEGREEN_EXTERNAL_PASSTHROUGH", raising=False)

    env = _minimal_env()

    assert "OPENAI_API_KEY" not in env
    assert "RACEGREEN_API_KEY" not in env


def test_external_agent_environment_honors_explicit_passthrough(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("RACEGREEN_EXTERNAL_PASSTHROUGH", "OPENAI_API_KEY")

    env = _minimal_env()

    assert env["OPENAI_API_KEY"] == "secret"
