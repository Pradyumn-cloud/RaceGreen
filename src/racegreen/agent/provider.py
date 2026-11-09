from __future__ import annotations

import json
import os
import threading
from collections.abc import Sequence
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .config import ProviderConfig
from .models import ProviderResponse, ToolCall


class ProviderError(RuntimeError):
    """Raised when a provider cannot produce a valid response."""


class ProviderTimeout(ProviderError):
    """Raised when a provider call exceeds the agent's remaining budget."""


_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def _read_limited(response: object, limit: int = _MAX_RESPONSE_BYTES) -> bytes:
    read = getattr(response, "read")
    data = read(limit + 1)
    if not isinstance(data, bytes):
        raise ProviderError("provider response is not bytes")
    if len(data) > limit:
        raise ProviderError("provider response exceeds the size limit")
    return data


def _validate_remote_transport(endpoint: str) -> None:
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise ValueError("provider endpoint must be an absolute HTTP(S) URL")
    loopback = {"127.0.0.1", "localhost", "::1"}
    allow_insecure = os.environ.get("RACEGREEN_ALLOW_INSECURE_HTTP", "").strip() in {
        "1",
        "true",
        "yes",
    }
    if parsed.scheme == "http" and parsed.hostname not in loopback and not allow_insecure:
        raise ValueError(
            "remote provider endpoints must use HTTPS; for trusted local HTTP providers "
            "(e.g. WSL to host Ollama) set RACEGREEN_ALLOW_INSECURE_HTTP=1"
        )


class Provider(Protocol):
    def complete(self, messages: Sequence[dict[str, str]], cancel_event: threading.Event | None = None) -> ProviderResponse:
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
        requires_api_key: bool | None = None,
    ) -> None:
        parsed = urlparse(endpoint)
        _validate_remote_transport(endpoint)
        loopback = {"127.0.0.1", "localhost", "::1"}
        self.endpoint = endpoint.rstrip("/")
        if not self.endpoint.endswith("/v1"):
            self.endpoint += "/v1"
        self.endpoint += "/chat/completions"
        self.model = model
        self.api_key_env = api_key_env
        self.timeout_seconds = timeout_seconds
        self.requires_api_key = (
            requires_api_key
            if requires_api_key is not None
            else parsed.scheme == "https" and parsed.hostname not in loopback
        )

    def complete(self, messages: Sequence[dict[str, str]], cancel_event: threading.Event | None = None) -> ProviderResponse:
        if cancel_event is not None and cancel_event.is_set():
            raise ProviderError("cancelled")
        api_key = os.environ.get(self.api_key_env)
        if self.requires_api_key and not api_key:
            raise ProviderError(f"provider credential is missing: {self.api_key_env}")
        body = json.dumps(
            {"model": self.model, "messages": list(messages), "tools": _tool_definitions()}
        ).encode()
        headers = {
            "Content-Type": "application/json",
        }
        if api_key:
            headers["Authorization"] = "Bearer " + api_key
        request = Request(
            self.endpoint,
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                if cancel_event is not None and cancel_event.is_set():
                    raise ProviderError("cancelled")
                payload = json.loads(_read_limited(response))
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            if cancel_event is not None and cancel_event.is_set():
                raise ProviderError("cancelled")
            detail = str(exc).strip().replace("\n", " ")
            if len(detail) > 200:
                detail = detail[:200] + "..."
            raise ProviderError(f"provider request failed: {type(exc).__name__}: {detail}") from exc
        try:
            message = payload["choices"][0]["message"]
            raw_tool_calls = message.get("tool_calls") or []
            tool_calls = tuple(_parse_openai_tool_call(item) for item in raw_tool_calls)
            if not tool_calls:
                tool_calls = _parse_tool_calls_from_content(str(message.get("content") or ""))
            usage = payload.get("usage", {})
            return ProviderResponse(
                content=str(message.get("content") or ""),
                tool_calls=tool_calls,
                input_tokens=usage.get("prompt_tokens"),
                output_tokens=usage.get("completion_tokens"),
                finish_reason=payload["choices"][0].get("finish_reason"),
            )
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            if cancel_event is not None and cancel_event.is_set():
                raise ProviderError("cancelled")
            raise ProviderError("provider returned an invalid response") from exc


def probe_provider(config: ProviderConfig) -> dict[str, object]:
    """Make a bounded, credential-safe connectivity request for a provider config."""
    endpoint = config.endpoint
    api_key_env = config.api_key_env
    requires_api_key = config.requires_api_key
    timeout_seconds = config.timeout_seconds
    try:
        _validate_remote_transport(endpoint)
    except ValueError as exc:
        raise ProviderError(str(exc)) from exc
    api_key = os.environ.get(api_key_env)
    if requires_api_key and not api_key:
        raise ProviderError(f"provider credential is missing: {api_key_env}")
    parsed = urlparse(endpoint.rstrip("/"))
    if parsed.hostname in {"127.0.0.1", "localhost", "::1"} or not requires_api_key:
        url = endpoint.rstrip("/") + "/api/tags"
    else:
        url = endpoint.rstrip("/") + ("" if endpoint.rstrip("/").endswith("/v1") else "/v1") + "/models"
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    try:
        with urlopen(Request(url, headers=headers, method="GET"), timeout=timeout_seconds) as response:
            payload = json.loads(_read_limited(response))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        detail = str(exc).strip().replace("\n", " ")
        if len(detail) > 200:
            detail = detail[:200] + "..."
        raise ProviderError(f"provider probe failed: {type(exc).__name__}: {detail}") from exc
    if not isinstance(payload, dict):
        raise ProviderError("provider probe returned an invalid response")
    models = payload.get("models") or payload.get("data") or []
    return {"endpoint": endpoint, "reachable": True, "models": models}


def _parse_openai_tool_call(item: dict[str, object]) -> ToolCall:
    function = item.get("function")
    if not isinstance(function, dict):
        raise TypeError("tool call missing function")
    name = str(function.get("name") or "")
    if not name:
        raise ValueError("tool call missing name")
    raw_args = function.get("arguments", {})
    if isinstance(raw_args, str):
        args = json.loads(raw_args) if raw_args.strip() else {}
    elif isinstance(raw_args, dict):
        args = raw_args
    else:
        raise TypeError("tool call arguments must be object or JSON string")
    if not isinstance(args, dict):
        raise TypeError("tool call arguments must decode to an object")
    return ToolCall(name, args)


def _parse_tool_calls_from_content(content: str) -> tuple[ToolCall, ...]:
    text = content.strip()
    if not text:
        return ()
    candidates: list[object] = []
    try:
        parsed = json.loads(text)
        candidates.append(parsed)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        idx = 0
        while idx < len(text):
            while idx < len(text) and text[idx].isspace():
                idx += 1
            if idx >= len(text):
                break
            if text[idx] not in "{[":
                # Try line-based JSON objects for some local model outputs.
                nl = text.find("\n", idx)
                if nl == -1:
                    break
                idx = nl + 1
                continue
            try:
                obj, end = decoder.raw_decode(text, idx)
                candidates.append(obj)
                idx = end
            except json.JSONDecodeError:
                nl = text.find("\n", idx)
                if nl == -1:
                    break
                idx = nl + 1
    calls: list[ToolCall] = []
    for item in candidates:
        if isinstance(item, list):
            for sub in item:
                call = _coerce_tool_call(sub)
                if call is not None:
                    calls.append(call)
        else:
            call = _coerce_tool_call(item)
            if call is not None:
                calls.append(call)
    return tuple(calls)


def _coerce_tool_call(item: object) -> ToolCall | None:
    if not isinstance(item, dict):
        return None
    name = item.get("name")
    args = item.get("arguments")
    if not isinstance(name, str) or not name:
        function = item.get("function")
        if isinstance(function, dict) and isinstance(function.get("name"), str):
            name = function["name"]
            args = function.get("arguments", args)
    if not isinstance(name, str) or not name:
        return None
    if args is None:
        args = {}
    if isinstance(args, str):
        args = json.loads(args) if args.strip() else {}
    if not isinstance(args, dict):
        return None
    return ToolCall(name, args)


def _tool_definitions() -> list[dict[str, object]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "read",
                "description": "Read a UTF-8 text file in the assigned worktree.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search",
                "description": "Search text in the assigned worktree.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                        "path": {"type": "string"},
                    },
                    "required": ["pattern"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "edit",
                "description": "Create or replace a UTF-8 text file in the assigned worktree.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "command",
                "description": "Run a bounded command using an argument array.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "argv": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["argv"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "submit_candidate",
                "description": "Submit the current worktree as an immutable candidate patch.",
                "parameters": {
                    "type": "object",
                    "properties": {"candidate_id": {"type": "string"}},
                },
            },
        },
    ]
