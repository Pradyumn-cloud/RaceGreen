"""Shared rate limiter for multi-agent runs."""

from __future__ import annotations

import threading
import time


class SharedRateLimiter:
    def __init__(self, max_per_second: float = 1.0, burst: int = 1) -> None:
        self.max_per_second = max_per_second
        self.burst = burst
        self._lock = threading.RLock()
        self._tokens = float(burst)
        self._last = time.monotonic()
        self._backoff_until = 0.0

    def acquire(self, timeout: float | None = None) -> bool:
        start = time.monotonic()
        while True:
            with self._lock:
                now = time.monotonic()
                if now < self._backoff_until:
                    wait = self._backoff_until - now
                else:
                    elapsed = now - self._last
                    self._last = now
                    self._tokens = min(self.burst, self._tokens + elapsed * self.max_per_second)
                    if self._tokens >= 1.0:
                        self._tokens -= 1.0
                        return True
                    wait = (1.0 - self._tokens) / self.max_per_second if self.max_per_second > 0 else 0.1
            if timeout is not None and (time.monotonic() - start) >= timeout:
                return False
            time.sleep(min(wait, 0.01))

    def record_rate_limited(self, retry_after: float | None = None) -> None:
        with self._lock:
            delay = retry_after if retry_after and retry_after > 0 else 2.0
            self._backoff_until = max(self._backoff_until, time.monotonic() + delay)
            # bounded backoff already applied via max/backoff_until
