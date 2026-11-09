"""Prepared verification environments with isolation."""

from __future__ import annotations

import hashlib
import os
import shutil
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


_ENV_ALLOWLIST = (
    "PATH",
    "PATHEXT",
    "HOME",
    "USER",
    "USERNAME",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "APPDATA",
    "LOCALAPPDATA",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "VIRTUAL_ENV",
    "CONDA_PREFIX",
    "PYTHONIOENCODING",
    "PYTHONUTF8",
    "PYTHONHASHSEED",
    "PYTHONPATH",
)

_ENV_PASSTHROUGH_PREFIXES = ("RACEGREEN_VERIFICATION_",)


def cleanup_verification_env(env: dict[str, str]) -> None:
    """Remove the private temp directory created for one verification run."""
    path = env.get("TMPDIR")
    if not path:
        return
    with _lock:
        owned = path in _tmpdirs
        _tmpdirs.discard(path)
    if owned:
        shutil.rmtree(path, ignore_errors=True)


def verification_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    """Return a minimal environment for untrusted test execution.

    Provider credentials, cloud tokens, and unrelated host variables are not
    inherited. Callers that need extra values can pass an explicit mapping.
    """
    source = base_env if base_env is not None else os.environ
    env = {
        key: value
        for key, value in source.items()
        if key in _ENV_ALLOWLIST or key.startswith(_ENV_PASSTHROUGH_PREFIXES)
    }
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["TMPDIR"] = make_tmpdir()
    env["TEMP"] = env["TMPDIR"]
    env["TMP"] = env["TMPDIR"]
    return env


def sandbox_env(env: dict[str, str]) -> dict[str, str]:
    """Adapt a host verification environment for a Docker mount.

    The host private temp directory is not mounted into the container. Point
    temporary-file variables at the container's writable ``/tmp`` tmpfs instead
    of leaking a host-only path that the test runner cannot use.
    """
    adapted = dict(env)
    adapted["TMPDIR"] = "/tmp"
    adapted["TEMP"] = "/tmp"
    adapted["TMP"] = "/tmp"
    return adapted
