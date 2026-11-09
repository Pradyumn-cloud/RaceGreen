from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

CONFIG_SCHEMA_VERSION = 1
_SECRET_KEYS = (
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "credential",
    "authorization",
    "bearer",
    "private_key",
    "access_key",
)
_ALLOWED_SECRET_LIKE_KEYS = {"api_key_env", "requires_api_key"}


@dataclass(frozen=True)
class ProviderConfig:
    endpoint: str
    model: str
    api_key_env: str = "RACEGREEN_API_KEY"
    timeout_seconds: float = 60.0
    requires_api_key: bool = True
    config_path: Path | None = None
    repository_config_path: Path | None = None

    @classmethod
    def from_environment(cls, repository: Path | None = None) -> ProviderConfig:
        path = config_path()
        file_config = _read_config(path, reject_credentials=True)
        repo_path = repo_config_path(repository) if repository is not None else None
        if repository is not None:
            repo_config = load_repo_config(repository)
            file_config = {**file_config, **repo_config}
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
            config_path=path if path.is_file() else None,
            repository_config_path=repo_path if repo_path and repo_path.is_file() else None,
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


def default_config_path() -> Path:
    """Return the platform-appropriate user configuration path."""
    configured = os.environ.get("RACEGREEN_CONFIG")
    if configured:
        return Path(configured).expanduser().resolve()
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return (base / "racegreen" / "config.json").resolve()


def legacy_config_path() -> Path:
    return (Path.home() / ".config" / "racegreen" / "config.json").resolve()


def config_path() -> Path:
    """Return the active config path while preserving older installations."""
    primary = default_config_path()
    if primary.is_file() or os.environ.get("RACEGREEN_CONFIG"):
        return primary
    legacy = legacy_config_path()
    if os.name == "nt" and legacy.is_file() and not primary.is_file():
        return legacy
    return primary


def repo_config_path(repository: Path) -> Path:
    return (Path(repository).resolve() / ".racegreen" / "config.json")


def load_repo_config(repository: Path) -> dict[str, Any]:
    """Load a repository-local configuration file with credential rejection."""
    path = repo_config_path(repository)
    payload = _read_config(path, reject_credentials=True)
    _validate_config_types(payload, path)
    return payload


def read_config(path: Path) -> dict[str, Any]:
    payload = _read_config(path, reject_credentials=True)
    _validate_config_types(payload, path)
    return payload


def _config_path() -> Path:
    return config_path()


def _read_config(path: Path, *, reject_credentials: bool = False) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read RaceGreen configuration: {path}") from exc
    if not isinstance(payload, dict):
        raise TypeError(f"RaceGreen configuration must be a JSON object: {path}")
    if reject_credentials:
        _reject_credentials(payload, path)
    return payload


def _reject_credentials(payload: dict[str, Any], path: Path, prefix: str = "") -> None:
    for key, value in payload.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        lowered = str(key).lower()
        if lowered not in _ALLOWED_SECRET_LIKE_KEYS and any(
            marker in lowered for marker in _SECRET_KEYS
        ):
            raise ValueError(
                f"Configuration must not contain credentials ({name}) in {path}; "
                "use an environment-variable name such as api_key_env instead."
            )
        if isinstance(value, dict):
            _reject_credentials(value, path, name)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, dict):
                    _reject_credentials(item, path, f"{name}[{index}]")


def _validate_config_types(payload: dict[str, Any], path: Path) -> None:
    string_keys = (
        "provider_url",
        "model",
        "api_key_env",
        "verification_command",
        "test_command",
        "winner_policy",
        "sandbox",
        "external_agent",
        "artifacts_root",
        "sandbox_image",
        "sandbox_network",
        "sandbox_memory",
        "sandbox_cpus",
    )
    for key in string_keys:
        if key in payload and not isinstance(payload[key], str):
            raise TypeError(f"{key} must be a string in {path}")
    number_keys = (
        "provider_timeout_seconds",
        "hedge_delay_seconds",
    )
    for key in number_keys:
        if key in payload and not isinstance(payload[key], (int, float)):
            raise TypeError(f"{key} must be a number in {path}")
    int_keys = (
        "schema_version",
        "agents",
        "max_concurrency",
        "max_changed_files",
        "max_changed_lines",
        "max_patch_bytes",
        "consensus_quorum",
        "max_full_suite_runs",
    )
    for key in int_keys:
        if key in payload and not isinstance(payload[key], int):
            raise TypeError(f"{key} must be an integer in {path}")
    bool_keys = ("requires_api_key", "require_approval")
    for key in bool_keys:
        if key in payload and not isinstance(payload[key], bool):
            raise TypeError(f"{key} must be a boolean in {path}")
    if "agent_models" in payload:
        models = payload["agent_models"]
        if not isinstance(models, list) or not all(isinstance(item, str) for item in models):
            raise TypeError(f"agent_models must be a list of strings in {path}")


def _config_model(payload: dict[str, Any]) -> str | None:
    model = payload.get("model")
    if isinstance(model, str) and model and model != "set-via-environment":
        return model
    return None


def _requires_api_key(endpoint: str, payload: dict[str, Any]) -> bool:
    configured = payload.get("requires_api_key")
    if isinstance(configured, bool):
        return configured
    return endpoint.lower().startswith("https://") and not is_loopback(endpoint)


def is_loopback(endpoint: str) -> bool:
    """Return true only for exact loopback hostnames, not lookalike domains."""
    try:
        parsed = urlparse(endpoint if "://" in endpoint else f"//{endpoint}")
        host = parsed.hostname
    except ValueError:
        return False
    return host in {"127.0.0.1", "localhost", "::1"}


# Backwards-compatible private alias used by older callers/tests.
_is_loopback = is_loopback
