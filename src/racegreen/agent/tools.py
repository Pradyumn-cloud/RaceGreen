from __future__ import annotations

import subprocess
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
    def __init__(self, root: Path, *, timeout_seconds: float, max_output_bytes: int) -> None:
        self.root = root.resolve(strict=True)
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes

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
        if not target.exists() or not target.is_file():
            raise ToolError(f"cannot edit missing file: {path}")
        target.write_text(content, encoding="utf-8")
        return f"edited {path}"

    def command(self, argv: list[str]) -> str:
        if not argv or not all(isinstance(item, str) and item for item in argv):
            raise ToolError("command must be a non-empty argument array")
        result = subprocess.run(
            argv,
            cwd=self.root,
            check=False,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
        )
        output = (result.stdout + result.stderr)[-self.max_output_bytes :]
        if result.returncode != 0:
            raise ToolError(f"command exited {result.returncode}: {output}")
        return output

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
