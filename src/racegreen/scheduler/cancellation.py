"""Process registry and POSIX-only cancellation."""

from __future__ import annotations

import os
import signal
import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class ProcessRecord:
    pid: int
    pgid: int
    run_id: str
    agent_id: str
    kind: str  # agent|test|other


class CancellationCoordinator:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cancelled = threading.Event()
        self._cancelling = threading.Event()
        self._cancel_requested_at: float | None = None
        self._cancel_complete_at: float | None = None
        self._processes: dict[int, ProcessRecord] = {}
        self._late_rejections = 0

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    @property
    def is_cancelling(self) -> bool:
        return self._cancelling.is_set()

    def request_cancel(self) -> float:
        with self._lock:
            if self._cancel_requested_at is None:
                self._cancel_requested_at = time.monotonic()
            self._cancelling.set()
            self._cancelled.set()
            return self._cancel_requested_at

    def register_process(self, record: ProcessRecord) -> None:
        with self._lock:
            self._processes[record.pid] = record

    def unregister_process(self, pid: int) -> None:
        with self._lock:
            self._processes.pop(pid, None)

    def get_processes(self) -> list[ProcessRecord]:
        with self._lock:
            return list(self._processes.values())

    def terminate_all(self, grace_seconds: float = 2.0) -> tuple[int, int]:
        """POSIX-only termination of process groups. SIGTERM then SIGKILL."""
        with self._lock:
            procs = list(self._processes.values())
        terminated = 0
        survivors_before = 0
        # Send SIGTERM to process groups
        for rec in procs:
            try:
                if hasattr(os, "killpg") and rec.pgid > 0:
                    os.killpg(rec.pgid, signal.SIGTERM)
                else:
                    try:
                        os.kill(rec.pid, signal.SIGTERM)
                    except Exception:
                        pass
                terminated += 1
            except ProcessLookupError:
                pass
            except Exception:
                pass
        time.sleep(grace_seconds)
        # Send SIGKILL and scan survivors
        for rec in procs:
            try:
                if hasattr(os, "killpg") and rec.pgid > 0:
                    try:
                        os.killpg(rec.pgid, 0)
                        os.killpg(rec.pgid, signal.SIGKILL)
                        survivors_before += 1
                    except ProcessLookupError:
                        pass
                else:
                    try:
                        os.kill(rec.pid, 0)
                        try:
                            os.kill(rec.pid, signal.SIGKILL)
                        except Exception:
                            pass
                        survivors_before += 1
                    except ProcessLookupError:
                        pass
            except Exception:
                pass
        if self._cancel_complete_at is None:
            self._cancel_complete_at = time.monotonic()
        return terminated, survivors_before

    def cancel_latency(self) -> float | None:
        if self._cancel_requested_at is None or self._cancel_complete_at is None:
            return None
        return self._cancel_complete_at - self._cancel_requested_at

    def record_late_rejection(self) -> None:
        with self._lock:
            self._late_rejections += 1
