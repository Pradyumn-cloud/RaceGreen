"""Process registry and cross-platform process-tree cancellation."""

from __future__ import annotations

import os
import signal
import subprocess
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

    def wait(self, timeout: float) -> bool:
        """Wait up to ``timeout`` seconds for cancellation; return True if set."""
        return self._cancelled.wait(timeout=timeout)

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
        """Terminate registered process trees, then escalate to a hard kill."""
        with self._lock:
            procs = list(self._processes.values())
        terminated = 0
        survivors_before = 0
        windows = os.name == "nt"
        # Ask each process tree to stop first.
        for rec in procs:
            try:
                if windows:
                    subprocess.run(
                        ["taskkill", "/T", "/PID", str(rec.pid)],
                        capture_output=True,
                        check=False,
                        timeout=5,
                    )
                elif hasattr(os, "killpg") and rec.pgid > 0:
                    os.killpg(rec.pgid, signal.SIGTERM)
                else:
                    os.kill(rec.pid, signal.SIGTERM)
                terminated += 1
            except ProcessLookupError:
                pass
            except Exception:
                pass
        time.sleep(grace_seconds)
        # Force-kill trees that survived the grace period.
        for rec in procs:
            try:
                if windows:
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(rec.pid)],
                        capture_output=True,
                        check=False,
                        timeout=5,
                    )
                elif hasattr(os, "killpg") and rec.pgid > 0:
                    killpg = getattr(os, "killpg")
                    try:
                        killpg(rec.pgid, 0)
                        killpg(rec.pgid, getattr(signal, "SIGKILL", signal.SIGTERM))
                        survivors_before += 1
                    except ProcessLookupError:
                        pass
                else:
                    try:
                        os.kill(rec.pid, 0)
                        os.kill(rec.pid, getattr(signal, "SIGKILL", signal.SIGTERM))
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
