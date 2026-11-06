from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any


class ToolError(RuntimeError):
    """Raised for invalid tool input or an unsuccessful tool operation."""


def _inside(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if candidate != root and root not in candidate.parents:
        raise ToolError("path escapes the assigned worktree")
    return candidate


class WorkspaceTools:
    def __init__(
        self,
        root: Path,
        *,
        timeout_seconds: float,
        max_output_bytes: int,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.root = root.resolve(strict=True)
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.cancel_event = cancel_event or threading.Event()

    def read(self, path: str) -> str:
        target = _inside(self.root, path)
        if not target.is_file():
            raise ToolError(f"not a file: {path}")
        return target.read_text(encoding="utf-8")[: self.max_output_bytes]

    def search(self, pattern: str, path: str = ".") -> str:
        target = _inside(self.root, path)
        result = subprocess.run(
            ["rg", "--no-heading", "--line-number", pattern, str(target)],
            cwd=self.root,
            check=False,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
        )
        if result.returncode not in (0, 1):
            raise ToolError(result.stderr.strip() or "search failed")
        return (result.stdout or "")[: self.max_output_bytes]

    def edit(self, path: str, content: str) -> str:
        target = _inside(self.root, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"wrote {path}"

    def command(self, argv: list[str]) -> str:
        if not argv or not all(isinstance(item, str) and item for item in argv):
            raise ToolError("command must be a non-empty argument array")
        kwargs: dict[str, Any] = {
            "cwd": self.root,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
        }
        if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        process = subprocess.Popen(argv, **kwargs)
        output = [""]

        def drain_output() -> None:
            stream = process.stdout
            if stream is None:
                return
            for chunk in iter(lambda: stream.read(4096), ""):
                output[0] = (output[0] + chunk)[-self.max_output_bytes :]

        reader = threading.Thread(target=drain_output, daemon=True)
        reader.start()
        started = time.monotonic()
        while process.poll() is None:
            if self.cancel_event.is_set():
                _terminate_process(process)
                raise ToolError("command cancelled")
            if time.monotonic() - started >= self.timeout_seconds:
                _terminate_process(process)
                raise ToolError("command timed out")
            time.sleep(0.05)
        reader.join(timeout=2)
        if process.returncode is None:
            raise ToolError("command ended without an exit status")
        if process.returncode != 0:
            raise ToolError(f"command exited {process.returncode}: {output[0]}")
        return output[0]

    def execute(self, name: str, arguments: dict[str, Any]) -> str:
        if name == "read":
            return self.read(str(arguments["path"]))
        if name == "search":
            return self.search(str(arguments["pattern"]), str(arguments.get("path", ".")))
        if name == "edit":
            return self.edit(str(arguments["path"]), str(arguments["content"]))
        if name == "command":
            argv = arguments.get("argv")
            if not isinstance(argv, list):
                raise ToolError("command argv must be an array")
            return self.command(argv)
        raise ToolError(f"unknown tool: {name}")


def _terminate_process(process: subprocess.Popen[str]) -> None:
    if os.name == "nt":
        process.send_signal(getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM))
    else:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)
