"""Base commit baseline caching."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .junit import JUnitSummary, parse_junit


@dataclass
class BaseBaseline:
    base_commit: str
    failing_tests: set[str]
    summary: JUnitSummary
    cached_at: float


_CACHE: dict[str, BaseBaseline] = {}


def _key(repo: Path, base_commit: str, cmd: list[str], env_fp: str) -> str:
    h = hashlib.sha256()
    h.update(str(repo).encode())
    h.update(base_commit.encode())
    h.update(json.dumps(cmd).encode())
    h.update(env_fp.encode())
    return h.hexdigest()


def build_baseline(repo: Path, base_commit: str, full_suite_cmd: list[str], env: dict[str, str] | None = None) -> BaseBaseline:
    env = env or os.environ.copy()
    with tempfile.TemporaryDirectory(prefix="racegreen-base-") as td:
        junit = Path(td) / "junit.xml"
        cmd = list(full_suite_cmd) + [f"--junitxml={junit}"]
        subprocess.run(
            cmd, cwd=str(repo), env=env, capture_output=True, timeout=1800, check=False
        )
        summary = parse_junit(junit) if junit.exists() else JUnitSummary()
        failing = set()
        for c in summary.cases:
            if c["status"] in ("failed", "error"):
                failing.add(f"{c['classname']}::{c['name']}" if c.get("classname") else c["name"])
        bl = BaseBaseline(base_commit=base_commit, failing_tests=failing, summary=summary, cached_at=time.time())
        _CACHE[_key(repo, base_commit, full_suite_cmd, "default")] = bl
        return bl


def get_baseline(repo: Path, base_commit: str, full_suite_cmd: list[str], env_fp: str = "default") -> BaseBaseline | None:
    return _CACHE.get(_key(repo, base_commit, full_suite_cmd, env_fp))
