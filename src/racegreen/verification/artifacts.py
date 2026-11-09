"""Bounded artifact capture."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


def read_bounded(path: Path, max_bytes: int) -> bytes:
    """Read at most ``max_bytes`` plus one byte from an untrusted file."""
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    with path.open("rb") as handle:
        return handle.read(max_bytes + 1)


def write_bounded(path: Path, data: bytes, max_bytes: int) -> str:
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    path.parent.mkdir(parents=True, exist_ok=True)
    if len(data) > max_bytes:
        marker = b"\n[TRUNCATED]\n"
        if max_bytes <= len(marker):
            data = marker[:max_bytes]
        else:
            data = data[: max_bytes - len(marker)] + marker
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return hashlib.sha256(path.read_bytes()).hexdigest()
