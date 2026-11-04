"""Security helpers shared by trace views and artifact exports."""

from __future__ import annotations

import re
from pathlib import Path

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")


def safe_terminal_text(value: object, limit: int = 20_000) -> str:
    text = str(value)
    text = _ANSI.sub("", text)
    text = _CONTROL.sub("", text)
    return text[:limit] + ("...[truncated]" if len(text) > limit else "")


def authorize_artifact(run_root: Path, requested: Path) -> Path:
    root = run_root.expanduser().resolve(strict=True)
    candidate = requested if requested.is_absolute() else root / requested
    current = candidate
    while current != root and root in current.parents:
        if current.is_symlink():
            raise ValueError("symlink artifacts are not permitted")
        current = current.parent
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError("artifact does not exist") from exc
    if resolved != root and root not in resolved.parents:
        raise ValueError("artifact path escapes the run directory")
    return resolved
