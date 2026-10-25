from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderConfig:
    endpoint: str
    model: str
    api_key_env: str = "RACEGREEN_API_KEY"
    timeout_seconds: float = 60.0

    @classmethod
    def from_environment(cls) -> ProviderConfig:
        endpoint = os.environ.get("RACEGREEN_PROVIDER_URL", "https://api.openai.com")
        model = os.environ.get("RACEGREEN_MODEL")
        if not model:
            raise ValueError("RACEGREEN_MODEL must be set")
        return cls(
            endpoint=endpoint,
            model=model,
            api_key_env=os.environ.get("RACEGREEN_API_KEY_ENV", "RACEGREEN_API_KEY"),
        )

    def fingerprint(self) -> str:
        payload = {
            "endpoint": self.endpoint,
            "model": self.model,
            "api_key_env": self.api_key_env,
            "timeout_seconds": self.timeout_seconds,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
