from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from ..verification.process import run_bounded_command


class ToolError(RuntimeError):
    """Raised for invalid tool input or an unsuccessful tool operation."""


_TOOL_ENV_KEYS = (
    "PATH",
    "PATHEXT",
    "HOME",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
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
    "USER",
    "USERNAME",
)


def _tool_env() -> dict[str, str]:
    """Return a small allowlisted environment for agent tool commands."""
    env = {
        key: value
        for key in _TOOL_ENV_KEYS
        if (value := os.environ.get(key)) is not None
    }
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    return env


def _inside(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if candidate != root and root not in candidate.parents:
        raise ToolError("path escapes the assigned worktree")
    return candidate


def _relative_path(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root))
    except ValueError:
        return str(path)


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
        try:
            with target.open("r", encoding="utf-8", errors="replace") as handle:
                return handle.read(self.max_output_bytes)
        except OSError as exc:
            raise ToolError(f"unable to read {path}: {exc}") from exc

    def search(self, pattern: str, path: str = ".") -> str:
        target = _inside(self.root, path)
        rg = shutil.which("rg")
        if rg is None:
            return self._python_search(pattern, target)
        result = run_bounded_command(
            [rg, "--no-heading", "--line-number", "--", pattern, str(target)],
            cwd=self.root,
            env=_tool_env(),
            timeout_seconds=self.timeout_seconds,
            max_output_bytes=self.max_output_bytes,
        )
        if result.cancelled:
            raise ToolError("search cancelled")
        if result.timed_out:
            raise ToolError("search timed out")
        if result.returncode not in (0, 1):
            raise ToolError(result.stderr.strip() or "search failed")
        return result.stdout[: self.max_output_bytes]

    def _python_search(self, pattern: str, target: Path) -> str:
        """Pure-Python ripgrep fallback for hosts without ripgrep installed."""
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise ToolError(f"invalid search pattern: {exc}") from exc
        skip_dirs = {
            ".git",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            ".venv",
            "venv",
            "node_modules",
        }
        files: list[Path]
        if target.is_file():
            files = [target]
        elif target.is_dir():
            files = []
            for root, dirnames, filenames in os.walk(target):
                dirnames[:] = [
                    name for name in dirnames if name not in skip_dirs and not name.startswith(".")
                ]
                for filename in filenames:
                    files.append(Path(root) / filename)
        else:
            raise ToolError(f"not a file or directory: {_relative_path(target, self.root)}")
        lines: list[str] = []
        used = 0
        for file_path in files:
            try:
                resolved = file_path.resolve(strict=True)
                if resolved != self.root and self.root not in resolved.parents:
                    continue
                if resolved.stat().st_size > 5 * 1024 * 1024:
                    continue
                raw = resolved.read_bytes()
            except OSError:
                continue
            if b"\x00" in raw[:8192]:
                continue
            text = raw.decode("utf-8", errors="replace")
            relative = _relative_path(file_path, self.root)
            for number, line in enumerate(text.splitlines(), start=1):
                if not regex.search(line):
                    continue
                rendered = f"{relative}:{number}:{line}\n"
                used += len(rendered)
                if used > self.max_output_bytes:
                    return "".join(lines)[: self.max_output_bytes]
                lines.append(rendered)
        return "".join(lines)[: self.max_output_bytes]

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
            "env": _tool_env(),
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
        # CTRL_BREAK_EVENT does not reliably reach grandchildren. taskkill /T
        # terminates the whole process tree created for this tool command.
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        killpg = getattr(os, "killpg", None)
        if killpg is not None:
            killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)
