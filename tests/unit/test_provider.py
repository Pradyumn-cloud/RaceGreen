from __future__ import annotations

import pytest

from racegreen.agent.config import ProviderConfig
from racegreen.agent.provider import OpenAICompatibleProvider, ProviderError


def test_provider_config_requires_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RACEGREEN_MODEL", raising=False)
    with pytest.raises(ValueError, match="RACEGREEN_MODEL"):
        ProviderConfig.from_environment()


def test_local_http_endpoint_is_allowed_without_exposing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RACEGREEN_API_KEY", raising=False)
    provider = OpenAICompatibleProvider(
        endpoint="http://127.0.0.1:11434",
        model="qwen2.5-coder:7b",
    )
    assert provider.endpoint == "http://127.0.0.1:11434/v1/chat/completions"
    with pytest.raises(ProviderError, match="credential is missing"):
        provider.complete([])


def test_remote_http_endpoint_is_rejected() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        OpenAICompatibleProvider(endpoint="http://provider.example", model="model")
