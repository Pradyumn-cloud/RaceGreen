"""Optional Docker wrapper for verification commands.

The sandbox mounts only the disposable verification checkout at ``/work`` and
runs with ``--network none`` by default. It does not make an untrusted
repository safe by itself: the image, Docker daemon, and mounted worktree are
still part of the trust boundary. See SECURITY.md.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DockerSandbox:
    image: str
    network: str = "none"
    memory: str | None = None
    cpus: str | None = None
    read_only_root: bool = True

    def __post_init__(self) -> None:
        if not self.image.strip():
            raise ValueError("sandbox image must not be empty")
        if self.network not in {"none", "bridge", "host"}:
            raise ValueError("sandbox network must be none, bridge, or host")

    def validate_available(self) -> None:
        if shutil.which("docker") is None:
            raise RuntimeError("docker sandbox requested but the docker executable is not available")

    def wrap(self, command: list[str], *, worktree: Path) -> list[str]:
        self.validate_available()
        mount = f"{Path(worktree).resolve()}:/work"
        wrapped = [
            "docker",
            "run",
            "--rm",
            "--network",
            self.network,
            "-v",
            mount,
            "-w",
            "/work",
        ]
        if self.read_only_root:
            wrapped.append("--read-only")
            # Test runners commonly need a writable temp directory even when
            # the source checkout itself is mounted writable.
            wrapped.extend(["--tmpfs", "/tmp:rw,noexec,nosuid,size=256m"])
        if self.memory:
            wrapped.extend(["--memory", self.memory])
        if self.cpus:
            wrapped.extend(["--cpus", self.cpus])
        wrapped.append(self.image)
        wrapped.extend(command)
        return wrapped

    def fingerprint(self) -> str:
        payload = {
            "image": self.image,
            "network": self.network,
            "memory": self.memory,
            "cpus": self.cpus,
            "read_only_root": self.read_only_root,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
