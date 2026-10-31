"""Prepared verification environments with isolation."""

from __future__ import annotations

import hashlib
import os
import socket
import tempfile
import threading

_lock = threading.RLock()
_port_base = 40000
_used_ports: set[int] = set()
_tmpdirs: set[str] = set()


def env_key(base_commit: str, lockfile_hash: str) -> str:
    h = hashlib.sha256()
    h.update(base_commit.encode())
    h.update(lockfile_hash.encode())
    return h.hexdigest()


def alloc_port() -> int:
    with _lock:
        for i in range(1000):
            port = _port_base + i
            if port in _used_ports:
                continue
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.bind(("127.0.0.1", port))
                s.close()
                _used_ports.add(port)
                return port
            except Exception:
                s.close()
                continue
    raise RuntimeError("no free port")


def free_port(port: int) -> None:
    with _lock:
        _used_ports.discard(port)


def make_tmpdir() -> str:
    d = tempfile.mkdtemp(prefix="racegreen-ver-")
    _tmpdirs.add(d)
    return d


def verification_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(base_env or os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["TMPDIR"] = make_tmpdir()
    env["TEMP"] = env["TMPDIR"]
    env["TMP"] = env["TMPDIR"]
    return env
