from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProviderConfig:
    endpoint: str
    model: str
    api_key_env: str = "RACEGREEN_API_KEY"
    timeout_seconds: float = 60.0
    requires_api_key: bool = True
    config_path: Path | None = None

    @classmethod
    def from_environment(cls) -> ProviderConfig:
        config_path = _config_path()
        file_config = _read_config(config_path)
        endpoint = os.environ.get(
            "RACEGREEN_PROVIDER_URL",
            str(file_config.get("provider_url", "https://api.openai.com")),
        )
        model = os.environ.get("RACEGREEN_MODEL") or _config_model(file_config)
        if not model:
            raise ValueError(
                "No model configured. Run `racegreen setup` or set RACEGREEN_MODEL."
            )
        timeout_raw = os.environ.get(
            "RACEGREEN_PROVIDER_TIMEOUT_SECONDS",
            str(file_config.get("provider_timeout_seconds", 60.0)),
        )
        timeout_seconds = 60.0
        if timeout_raw:
            try:
                timeout_seconds = max(1.0, float(timeout_raw))
            except ValueError as exc:
                raise ValueError("RACEGREEN_PROVIDER_TIMEOUT_SECONDS must be a number") from exc
        return cls(
            endpoint=endpoint,
            model=model,
            api_key_env=os.environ.get(
                "RACEGREEN_API_KEY_ENV",
                str(file_config.get("api_key_env", "RACEGREEN_API_KEY")),
            ),
            timeout_seconds=timeout_seconds,
            requires_api_key=_requires_api_key(endpoint, file_config),
            config_path=config_path if config_path.is_file() else None,
        )

    def fingerprint(self) -> str:
        payload = {
            "endpoint": self.endpoint,
            "model": self.model,
            "api_key_env": self.api_key_env,
            "timeout_seconds": self.timeout_seconds,
            "requires_api_key": self.requires_api_key,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _config_path() -> Path:
    configured = os.environ.get("RACEGREEN_CONFIG")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".config" / "racegreen" / "config.json").resolve()


def _read_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read RaceGreen configuration: {path}") from exc
    if not isinstance(payload, dict):
        raise TypeError(f"RaceGreen configuration must be a JSON object: {path}")
    return payload


def _config_model(payload: dict[str, Any]) -> str | None:
    model = payload.get("model")
    if isinstance(model, str) and model and model != "set-via-environment":
        return model
    return None


def _requires_api_key(endpoint: str, payload: dict[str, Any]) -> bool:
    configured = payload.get("requires_api_key")
    if isinstance(configured, bool):
        return configured
    return endpoint.lower().startswith("https://") and not _is_loopback(endpoint)


def _is_loopback(endpoint: str) -> bool:
    lowered = endpoint.lower()
    return any(host in lowered for host in ("127.0.0.1", "localhost", "[::1]"))
