"""Base commit baseline caching.

The baseline is always measured in a temporary clone of the source repository
at the captured base commit. Running the suite directly in the user's source
tree would mutate caches and could race with other work.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .env import sandbox_env
from .junit import JUnitSummary, parse_junit, with_junit_output
from .process import run_bounded_command
from .sandbox import DockerSandbox

_BASELINE_TIMEOUT_SECONDS = 1800.0


@dataclass
class BaseBaseline:
    base_commit: str
    failing_tests: set[str]
    summary: JUnitSummary
    cached_at: float
    returncode: int = 0
    junit_present: bool = False
    failures_by_case: dict[str, str] = field(default_factory=dict)
    timed_out: bool = False


_CACHE: dict[str, BaseBaseline] = {}
_CACHE_LOCK = threading.Lock()


def _prepare_output(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _key(repo: Path, base_commit: str, cmd: list[str], env_fp: str, sandbox_fp: str) -> str:
    h = hashlib.sha256()
    h.update(str(repo.resolve()).encode())
    h.update(base_commit.encode())
    h.update(json.dumps(cmd).encode())
    h.update(env_fp.encode())
    h.update(sandbox_fp.encode())
    return h.hexdigest()


def _build_baseline_uncached(
    repo: Path,
    base_commit: str,
    full_suite_cmd: list[str],
    env: dict[str, str] | None = None,
    env_fp: str = "default",
    timeout: float = _BASELINE_TIMEOUT_SECONDS,
    sandbox: DockerSandbox | None = None,
    cancel_event: threading.Event | None = None,
) -> BaseBaseline:
    """Measure the base commit in a disposable checkout."""
    env = env or os.environ.copy()
    with tempfile.TemporaryDirectory(prefix="racegreen-base-") as td:
        checkout = Path(td) / "co"
        clone = run_bounded_command(
            [
                "git",
                "-c",
                "core.autocrlf=false",
                "-c",
                "core.eol=lf",
                "clone",
                "--no-checkout",
                "--no-hardlinks",
                str(repo),
                str(checkout),
            ],
            cwd=repo,
            env=env,
            timeout_seconds=120,
            cancel_event=cancel_event,
        )
        if clone.returncode != 0:
            raise RuntimeError(
                "baseline clone failed: " + clone.stderr.strip()
            )
        co = run_bounded_command(
            [
                "git",
                "-c",
                "core.autocrlf=false",
                "-c",
                "core.eol=lf",
                "-C",
                str(checkout),
                "checkout",
                "--detach",
                base_commit,
            ],
            cwd=repo,
            env=env,
            timeout_seconds=120,
            cancel_event=cancel_event,
        )
        if co.returncode != 0:
            raise RuntimeError(
                "baseline checkout failed: " + co.stderr.strip()
            )
        head = run_bounded_command(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"],
            cwd=repo,
            env=env,
            timeout_seconds=30,
            cancel_event=cancel_event,
        )
        if head.returncode != 0 or head.stdout.strip() != base_commit:
            raise RuntimeError("baseline checkout did not resolve the base commit")
        junit = checkout / ".racegreen-baseline-junit.xml"
        _prepare_output(junit)
        cmd = with_junit_output(full_suite_cmd, Path(junit.name))
        run_env = env
        if sandbox is not None:
            cmd = sandbox.wrap(cmd, worktree=checkout)
            run_env = sandbox_env(env)
        completed = run_bounded_command(
            cmd,
            cwd=checkout,
            env=run_env,
            timeout_seconds=timeout,
            cancel_event=cancel_event,
        )
        if completed.cancelled:
            raise RuntimeError("baseline cancelled")
        returncode = -9 if completed.timed_out else completed.returncode
        summary = parse_junit(junit) if junit.exists() else JUnitSummary()
        failing, failures_by_case = _failure_sets(summary)
        baseline = BaseBaseline(
            base_commit=base_commit,
            failing_tests=failing,
            summary=summary,
            cached_at=time.time(),
            returncode=returncode,
            junit_present=junit.exists(),
            failures_by_case=failures_by_case,
            timed_out=completed.timed_out,
        )
        return baseline


def build_baseline(
    repo: Path,
    base_commit: str,
    full_suite_cmd: list[str],
    env: dict[str, str] | None = None,
    env_fp: str = "default",
    timeout: float = _BASELINE_TIMEOUT_SECONDS,
    sandbox: DockerSandbox | None = None,
    cancel_event: threading.Event | None = None,
) -> BaseBaseline:
    """Measure a base commit once and return the cached result on later calls.

    Concurrent verification workers share a run. Serialize baseline creation so
    two candidates cannot launch duplicate full-suite baseline runs.
    """
    sandbox_fp = sandbox.fingerprint() if sandbox is not None else "none"
    key = _key(repo, base_commit, full_suite_cmd, env_fp, sandbox_fp)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            return cached
        baseline = _build_baseline_uncached(
            repo,
            base_commit,
            full_suite_cmd,
            env,
            env_fp,
            timeout,
            sandbox,
            cancel_event,
        )
        _CACHE[key] = baseline
        return baseline


def baseline_is_usable(baseline: BaseBaseline) -> bool:
    """Return whether a baseline is trustworthy enough for comparison.

    A missing or empty JUnit report, a timed-out suite, or a failed command
    without any recorded test failure means the base result is unknown. A
    candidate must not be confirmed against unknown baseline evidence.
    """
    if baseline.timed_out or not baseline.junit_present:
        return False
    summary = baseline.summary
    if summary.tests <= 0 and not summary.cases:
        return False
    if baseline.returncode != 0 and (summary.failures + summary.errors) == 0:
        return False
    return True


def get_baseline(
    repo: Path,
    base_commit: str,
    full_suite_cmd: list[str],
    env_fp: str = "default",
    sandbox: DockerSandbox | None = None,
) -> BaseBaseline | None:
    sandbox_fp = sandbox.fingerprint() if sandbox is not None else "none"
    with _CACHE_LOCK:
        return _CACHE.get(_key(repo, base_commit, full_suite_cmd, env_fp, sandbox_fp))


def _failure_sets(summary: JUnitSummary) -> tuple[set[str], dict[str, str]]:
    failing: set[str] = set()
    details: dict[str, str] = {}
    for case in summary.cases:
        status = str(case.get("status") or "")
        if status not in {"failed", "error"}:
            continue
        classname = str(case.get("classname") or "")
        name = str(case.get("name") or "")
        test_id = f"{classname}::{name}" if classname else name
        failing.add(test_id)
        details[test_id] = str(case.get("message") or status)
    return failing, details
