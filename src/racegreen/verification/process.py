"""Bounded command execution for untrusted verification commands."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BoundedCommandResult:
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False
    cancelled: bool = False


def _read_stream(stream: Any, max_bytes: int) -> bytes:
    """Drain a pipe while retaining at most ``max_bytes`` of its content."""
    kept = bytearray()
    while True:
        chunk = stream.read(64 * 1024)
        if not chunk:
            break
        remaining = max_bytes - len(kept)
        if remaining > 0:
            kept.extend(chunk[:remaining])
    return bytes(kept)


def _kill_tree(process: subprocess.Popen[bytes]) -> None:
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            pass
        return
    try:
        killpg = getattr(os, "killpg")
        getpgid = getattr(os, "getpgid")
        killpg(getpgid(process.pid), getattr(signal, "SIGKILL", signal.SIGTERM))
    except (ProcessLookupError, PermissionError, OSError):
        try:
            process.kill()
        except OSError:
            pass


def _wait_for_exit(process: subprocess.Popen[bytes]) -> None:
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


def run_bounded_command(
    command: list[str],
    *,
    cwd: str | Path,
    env: dict[str, str],
    timeout_seconds: float,
    max_output_bytes: int = 64 * 1024,
    cancel_event: threading.Event | None = None,
) -> BoundedCommandResult:
    """Run a command with a timeout and bounded stdout/stderr capture.

    Reader threads always drain the child pipes, so a noisy command cannot
    deadlock or exhaust memory. On timeout or cancellation the full process
    tree is terminated before the result is returned.
    """
    if max_output_bytes < 1:
        raise ValueError("max_output_bytes must be positive")
    started = time.monotonic()
    kwargs: dict[str, Any] = {
        "cwd": str(cwd),
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "shell": False,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True

    try:
        process = subprocess.Popen(command, **kwargs)
    except (OSError, ValueError) as exc:
        return BoundedCommandResult(
            -1,
            "",
            f"unable to start command: {type(exc).__name__}: {exc}",
            time.monotonic() - started,
        )

    stdout_holder: list[bytes] = []
    stderr_holder: list[bytes] = []

    def drain_stdout() -> None:
        assert process.stdout is not None
        stdout_holder.append(_read_stream(process.stdout, max_output_bytes))

    def drain_stderr() -> None:
        assert process.stderr is not None
        stderr_holder.append(_read_stream(process.stderr, max_output_bytes))

    stdout_thread = threading.Thread(target=drain_stdout, daemon=True)
    stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    timed_out = False
    cancelled = False
    deadline = started + timeout_seconds
    while True:
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            _kill_tree(process)
            _wait_for_exit(process)
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            _kill_tree(process)
            _wait_for_exit(process)
            break
        try:
            process.wait(timeout=min(0.2, remaining))
            break
        except subprocess.TimeoutExpired:
            continue

    stdout_thread.join(timeout=5)
    stderr_thread.join(timeout=5)
    if not stdout_thread.is_alive() and process.stdout is not None:
        process.stdout.close()
    if not stderr_thread.is_alive() and process.stderr is not None:
        process.stderr.close()
    stdout = (stdout_holder[0] if stdout_holder else b"").decode(
        "utf-8", errors="replace"
    )
    stderr = (stderr_holder[0] if stderr_holder else b"").decode(
        "utf-8", errors="replace"
    )
    return BoundedCommandResult(
        process.returncode if process.returncode is not None else -1,
        stdout,
        stderr,
        time.monotonic() - started,
        timed_out=timed_out,
        cancelled=cancelled,
    )
