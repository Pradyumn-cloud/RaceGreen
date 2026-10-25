from __future__ import annotations

import json
import os
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SECRET = re.compile(r"(?i)(api[_-]?key|token|secret|password)(\s*[:=]\s*)([^\s,]+)")


def _redact(value: object) -> object:
    if isinstance(value, str):
        return _SECRET.sub(r"\1\2[REDACTED]", value)
    if isinstance(value, dict):
        return {str(key): _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


class EventStore:
    """Durable append-only JSONL event storage for runs/agents."""

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._terminal = threading.Event()

    def mark_terminal(self) -> None:
        self._terminal.set()

    def is_terminal(self) -> bool:
        return self._terminal.is_set()

    def append_atomic(
        self,
        event: str,
        *,
        run_id: str,
        agent_id: str,
        cancelled: bool = False,
        late_check: bool = True,
        **data: Any,
    ) -> dict[str, Any] | None:
        """Append with atomic late-event rejection. Returns None if rejected as late."""
        with self._lock:
            if late_check and self._terminal.is_set():
                rejected = {
                    "schema_version": 1,
                    "event": "event.rejected_late",
                    "run_id": run_id,
                    "agent_id": agent_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "rejected_event": event,
                    **_redact(data),
                }
                try:
                    with self.path.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(rejected, sort_keys=True) + "\n")
                        stream.flush()
                        os.fsync(stream.fileno())
                except OSError as exc:
                    raise RuntimeError(f"Unable to persist event {event}: {self.path}") from exc
                return None
            record = {
                "schema_version": 1,
                "event": event,
                "run_id": run_id,
                "agent_id": agent_id,
                "timestamp": datetime.now(UTC).isoformat(),
                **data,
            }
            safe_record = _redact(record)
            if not isinstance(safe_record, dict):
                raise TypeError("event redaction returned an invalid record")
            try:
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(safe_record, sort_keys=True) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
            except OSError as exc:
                raise RuntimeError(f"Unable to persist event {event}: {self.path}") from exc
            return safe_record

    def append(self, event: str, *, run_id: str, agent_id: str, **data: Any) -> dict[str, Any]:
        result = self.append_atomic(event, run_id=run_id, agent_id=agent_id, late_check=False, **data)
        if result is None:  # should not happen if late_check False
            raise RuntimeError(f"Event append failed: {event}")
        return result

    def replay(self) -> list[dict[str, Any]]:
        try:
            with self.path.open(encoding="utf-8") as stream:
                return [json.loads(line) for line in stream if line.strip()]
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Unable to replay events: {self.path}") from exc
