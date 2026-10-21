from __future__ import annotations

import json
import os
from collections.abc import Sequence
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .models import ProviderResponse, ToolCall


class ProviderError(RuntimeError):
    """Raised when a provider cannot produce a valid response."""


class Provider(Protocol):
    def complete(self, messages: Sequence[dict[str, str]]) -> ProviderResponse:
        """Return one bounded model response."""


class OpenAICompatibleProvider:
    """Minimal OpenAI-compatible adapter with explicit credential and URL checks."""

    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        api_key_env: str = "RACEGREEN_API_KEY",
        timeout_seconds: float = 60.0,
    ) -> None:
        parsed = urlparse(endpoint)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc:
            raise ValueError("provider endpoint must be an absolute HTTP(S) URL")
        if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("remote provider endpoints must use HTTPS")
        self.endpoint = endpoint.rstrip("/") + "/v1/chat/completions"
        self.model = model
        self.api_key_env = api_key_env
        self.timeout_seconds = timeout_seconds

    def complete(self, messages: Sequence[dict[str, str]]) -> ProviderResponse:
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise ProviderError(f"provider credential is missing: {self.api_key_env}")
        body = json.dumps({"model": self.model, "messages": list(messages)}).encode()
        request = Request(
            self.endpoint,
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read())
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise ProviderError("provider request failed") from exc
        try:
            message = payload["choices"][0]["message"]
            tool_calls = tuple(
                ToolCall(
                    str(item["function"]["name"]),
                    json.loads(item["function"]["arguments"]),
                )
                for item in message.get("tool_calls", [])
            )
            usage = payload.get("usage", {})
            return ProviderResponse(
                content=str(message.get("content") or ""),
                tool_calls=tool_calls,
                input_tokens=usage.get("prompt_tokens"),
                output_tokens=usage.get("completion_tokens"),
                finish_reason=payload["choices"][0].get("finish_reason"),
            )
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderError("provider returned an invalid response") from exc
