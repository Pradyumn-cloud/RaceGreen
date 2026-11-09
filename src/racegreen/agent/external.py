"""Adapter for an external coding-agent command.

The command runs once inside the agent's private worktree. When it exits
successfully the adapter asks RaceGreen's normal candidate intake path to
snapshot the worktree, so external agents are verified and arbitrated exactly
like built-in agents.

An external command is not a sandbox. It inherits a deliberately small
environment allowlist, but it still runs with the current user's permissions
and can access the network. Use an OS-level sandbox for untrusted commands.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .models import ProviderResponse, ToolCall
from .provider import ProviderError

_BASE_ENV_KEYS = (
    "PATH",
    "PATHEXT",
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "TMP",
    "TEMP",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "VIRTUAL_ENV",
    "PYTHONPATH",
    "PYTHONHOME",
)
_MAX_OUTPUT_BYTES = 64 * 1024
_MAX_TASK_BYTES = 64 * 1024


def _task_from_messages(messages: Sequence[dict[str, str]]) -> str:
    """Return the most recent user task without unbounded environment growth."""
    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content") or "")[:_MAX_TASK_BYTES]
    return ""


class ExternalAgentProvider:
    """Run an external agent command and submit its worktree changes once."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        worktree: Path,
        run_id: str,
        agent_id: str,
        timeout_seconds: float = 300.0,
    ) -> None:
        if not command:
            raise ValueError("external agent command must not be empty")
        self.command = list(command)
        self.worktree = Path(worktree).resolve()
        if not self.worktree.is_dir():
            raise ValueError(f"external agent worktree does not exist: {self.worktree}")
        self.run_id = run_id
        self.agent_id = agent_id
        self.timeout_seconds = max(1.0, timeout_seconds)
        self.candidate_id = f"{run_id}-{agent_id}-{uuid.uuid4().hex[:10]}"
        self._started = False
        self._lock = threading.Lock()

    def complete(
        self,
        messages: Sequence[dict[str, str]],
        cancel_event: threading.Event | None = None,
    ) -> ProviderResponse:
        with self._lock:
            if self._started:
                return ProviderResponse(content="External agent already completed.")
            self._started = True
        if cancel_event is not None and cancel_event.is_set():
            raise ProviderError("cancelled")
        env = _minimal_env()
        env.update(
            {
                "RACEGREEN_TASK": _task_from_messages(messages),
                "RACEGREEN_RUN_ID": self.run_id,
                "RACEGREEN_AGENT_ID": self.agent_id,
                "RACEGREEN_WORKTREE": str(self.worktree),
            }
        )
        returncode, stdout, stderr = _run_command(
            self.command,
            cwd=self.worktree,
            timeout_seconds=self.timeout_seconds,
            cancel_event=cancel_event,
            env=env,
        )
        if cancel_event is not None and cancel_event.is_set():
            raise ProviderError("cancelled")
        if returncode != 0:
            detail = (stderr or stdout).strip().replace("\n", " ")
            if len(detail) > 300:
                detail = detail[:300] + "..."
            raise ProviderError(
                f"external agent exited with code {returncode}: {detail or 'no output'}"
            )
        return ProviderResponse(
            content="External agent command completed; submitting the worktree snapshot.",
            tool_calls=(
                ToolCall("submit_candidate", {"candidate_id": self.candidate_id}),
            ),
        )


def _run_command(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: float,
    cancel_event: threading.Event | None,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    env = _minimal_env() if env is None else env
    creationflags = 0
    start_new_session = False
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        start_new_session = True
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        try:
            process = subprocess.Popen(
                list(command),
                cwd=str(cwd),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                shell=False,
                start_new_session=start_new_session,
                creationflags=creationflags,
            )
        except (OSError, ValueError) as exc:
            raise ProviderError(f"unable to start external agent: {type(exc).__name__}: {exc}") from exc
        deadline = time.monotonic() + timeout_seconds
        while process.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                _kill_tree(process)
                _wait_for_exit(process)
                raise ProviderError("cancelled")
            if time.monotonic() >= deadline:
                _kill_tree(process)
                _wait_for_exit(process)
                raise ProviderError(f"external agent timed out after {timeout_seconds:.0f}s")
            time.sleep(0.05)
        stdout = _read_limited(stdout_file)
        stderr = _read_limited(stderr_file)
        return process.returncode or 0, stdout, stderr


def _read_limited(stream: Any) -> str:
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(max(0, size - _MAX_OUTPUT_BYTES))
    data: bytes = stream.read()
    return data.decode("utf-8", errors="replace")


def _wait_for_exit(process: subprocess.Popen[bytes]) -> None:
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


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


def _minimal_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for key in _BASE_ENV_KEYS:
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    explicit = os.environ.get("RACEGREEN_EXTERNAL_PASSTHROUGH", "")
    for key in (item.strip() for item in explicit.split(",")):
        if key and key in os.environ:
            env[key] = os.environ[key]
    # Python-based external agents are common; make sure the interpreter that
    # launched RaceGreen is reachable even when PATH is unusual.
    env.setdefault("PYTHONPATH", os.pathsep.join(sys.path))
    return env
