from __future__ import annotations

import pytest

from racegreen.agent.config import ProviderConfig
from racegreen.agent.provider import OpenAICompatibleProvider


def test_provider_config_requires_model(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RACEGREEN_MODEL", raising=False)
    monkeypatch.setenv("RACEGREEN_CONFIG", str(tmp_path / "missing.json"))
    with pytest.raises(ValueError, match="model"):
        ProviderConfig.from_environment()


def test_local_http_endpoint_is_allowed_without_exposing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RACEGREEN_API_KEY", raising=False)
    provider = OpenAICompatibleProvider(
        endpoint="http://127.0.0.1:11434",
        model="qwen2.5-coder:7b",
    )
    assert provider.endpoint == "http://127.0.0.1:11434/v1/chat/completions"
    assert provider.requires_api_key is False


def test_remote_http_endpoint_is_rejected() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        OpenAICompatibleProvider(endpoint="http://provider.example", model="model")


def test_config_file_supplies_model_and_local_key_policy(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config.json"
    config.write_text(
        '{"provider_url":"http://192.168.1.20:11434","model":"local-model",'
        '"requires_api_key":false}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("RACEGREEN_CONFIG", str(config))
    monkeypatch.delenv("RACEGREEN_MODEL", raising=False)
    loaded = ProviderConfig.from_environment()
    assert loaded.model == "local-model"
    assert loaded.requires_api_key is False
