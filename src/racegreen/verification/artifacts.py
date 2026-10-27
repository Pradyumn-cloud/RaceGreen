"""Bounded artifact capture."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


def write_bounded(path: Path, data: bytes, max_bytes: int) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if len(data) > max_bytes:
        data = data[:max_bytes] + b"\n[TRUNCATED]\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return hashlib.sha256(path.read_bytes()).hexdigest()
