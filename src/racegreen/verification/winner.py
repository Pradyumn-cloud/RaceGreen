"""Atomic winner slot."""

from __future__ import annotations

import threading
from typing import Any


class WinnerSlot:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._winner: dict[str, Any] | None = None

    def try_set_winner(self, candidate_info: dict[str, Any]) -> bool:
        with self._lock:
            if self._winner is not None:
                return False
            self._winner = dict(candidate_info)
            return True

    def get_winner(self) -> dict[str, Any] | None:
        with self._lock:
            return dict(self._winner) if self._winner is not None else None

    def has_winner(self) -> bool:
        with self._lock:
            return self._winner is not None
