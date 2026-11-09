"""Run-scoped budget for expensive full-suite verification."""

from __future__ import annotations

import threading


class FullSuiteBudgetExhausted(RuntimeError):
    """Raised when a run has used all of its full-suite verification attempts."""


class VerificationBudget:
    """Thread-safe counter shared by every candidate in one run.

    A budget of ``None`` means unlimited. The counter is intentionally
    conservative: the caller must acquire a slot before starting the full
    suite, so a burst of concurrent candidates cannot overshoot the bound.
    """

    def __init__(self, max_full_suite_runs: int | None = 10) -> None:
        if max_full_suite_runs is not None and max_full_suite_runs < 1:
            raise ValueError("max_full_suite_runs must be at least 1 or None")
        self._max = max_full_suite_runs
        self._used = 0
        self._lock = threading.RLock()

    def try_acquire_full_suite(self) -> bool:
        with self._lock:
            if self._max is not None and self._used >= self._max:
                return False
            self._used += 1
            return True

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    @property
    def remaining(self) -> int | None:
        with self._lock:
            if self._max is None:
                return None
            return max(0, self._max - self._used)
