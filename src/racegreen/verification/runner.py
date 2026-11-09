"""Staged verification runner.

A candidate is verified only from an immutable patch file and a captured base
commit. Clone, checkout, and apply failures are rejections, not silent
successes. The full suite is parsed from JUnit XML and compared with a cached
base-commit baseline so a candidate that introduces new failures cannot be
confirmed.
"""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from ..agent.events import EventStore
from .artifacts import read_bounded, write_bounded
from .baseline import baseline_is_usable, build_baseline, get_baseline
from .budget import VerificationBudget
from .classifier import FailureClass, classify
from .config import VerificationConfig
from .env import cleanup_verification_env, sandbox_env, verification_env
from .evidence import VerificationEvidence
from .junit import JUnitSummary, parse_junit, with_junit_output
from .process import run_bounded_command
from .retry import RetryPolicy
from .sandbox import DockerSandbox
from .selection import TestSelector
from .winner import WinnerSlot


def _failing_tests(summary: JUnitSummary) -> set[str]:
    failing: set[str] = set()
    for case in summary.cases:
        if str(case.get("status") or "") not in {"failed", "error"}:
            continue
        classname = str(case.get("classname") or "")
        name = str(case.get("name") or "")
        failing.add(f"{classname}::{name}" if classname else name)
    return failing


def _patch_changed_lines(patch_bytes: bytes) -> int:
    total = 0
    for line in patch_bytes.splitlines():
        if line.startswith((b"+++", b"---")):
            continue
        if line.startswith((b"+", b"-")):
            total += 1
    return total


def _failing_hash(failing: set[str]) -> str:
    return hashlib.sha256("\n".join(sorted(failing)).encode()).hexdigest()


def _env_fingerprint(env: dict[str, str]) -> str:
    keys = (
        "PATH",
        "VIRTUAL_ENV",
        "CONDA_PREFIX",
        "PYTHONHASHSEED",
        "PYTHONPATH",
        "SYSTEMROOT",
        "COMSPEC",
        "LANG",
        "LC_ALL",
    )
    payload = {key: env.get(key) for key in keys if key in env}
    payload["python"] = sys.version
    payload["platform"] = platform.platform()
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _case_count(summary: JUnitSummary) -> int:
    return max(summary.tests, len(summary.cases))


def _prepare_output(path: Path) -> None:
    """Remove a candidate-created output path before writing trusted evidence.

    A patch can create a symlink at the JUnit path. Writing through it would
    let untrusted candidate content choose a host path, so always clear the
    path first and let the test runner create a regular file.
    """
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


class VerificationRunner:
    def __init__(
        self,
        events: EventStore | None = None,
        config: VerificationConfig | None = None,
        winner_slot: WinnerSlot | None = None,
        budget: VerificationBudget | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.events = events
        self.config = config or VerificationConfig()
        self.selector = TestSelector(self.config.use_selection)
        self.retry = RetryPolicy(self.config.max_retries)
        # Kept for API compatibility; arbitration is performed by the arbiter.
        self.winner_slot = winner_slot or WinnerSlot()
        self.budget = budget or VerificationBudget()
        self.cancel_event = cancel_event or threading.Event()
        if self.config.sandbox not in {"none", "docker"}:
            raise ValueError("sandbox must be 'none' or 'docker'")
        if self.config.sandbox == "docker":
            if not self.config.sandbox_image:
                raise ValueError("sandbox_image is required when sandbox is 'docker'")
            self.sandbox: DockerSandbox | None = DockerSandbox(
                image=self.config.sandbox_image,
                network=self.config.sandbox_network,
                memory=self.config.sandbox_memory,
                cpus=self.config.sandbox_cpus,
            )
            self.sandbox.validate_available()
        else:
            self.sandbox = None

    def cancel(self) -> None:
        """Stop any running verification command and refuse new ones."""
        self.cancel_event.set()

    def _run_cmd(
        self, cmd: list[str], cwd: str, env: dict[str, str], timeout: float
    ) -> tuple[int, str, str, float]:
        if self.cancel_event.is_set():
            return (-9, "", "cancelled", 0.0)
        if self.sandbox is not None:
            cmd = self.sandbox.wrap(cmd, worktree=Path(cwd))
            env = sandbox_env(env)
        result = run_bounded_command(
            cmd,
            cwd=cwd,
            env=env,
            timeout_seconds=timeout,
            max_output_bytes=64 * 1024,
            cancel_event=self.cancel_event,
        )
        if result.cancelled:
            return (
                -9,
                result.stdout,
                result.stderr + "\ncancelled",
                result.duration_seconds,
            )
        if result.timed_out:
            return (
                -9,
                result.stdout,
                result.stderr + "\ntimeout",
                result.duration_seconds,
            )
        return (
            result.returncode,
            result.stdout,
            result.stderr,
            result.duration_seconds,
        )

    @staticmethod
    def _git(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "git",
                "-c",
                "core.autocrlf=false",
                "-c",
                "core.eol=lf",
                *args,
            ],
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    def verify_candidate(
        self,
        repo: Path,
        worktree: Path,
        candidate_id: str,
        patch_path: Path,
        patch_hash: str,
        base_commit: str,
        run_id: str,
        agent_id: str,
        changed_paths: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        env = verification_env()
        try:
            return self._verify_candidate(
                repo,
                worktree,
                candidate_id,
                patch_path,
                patch_hash,
                base_commit,
                run_id,
                agent_id,
                changed_paths,
                env=env,
            )
        finally:
            cleanup_verification_env(env)

    def _verify_candidate(
        self,
        repo: Path,
        worktree: Path,
        candidate_id: str,
        patch_path: Path,
        patch_hash: str,
        base_commit: str,
        run_id: str,
        agent_id: str,
        changed_paths: tuple[str, ...] = (),
        *,
        env: dict[str, str],
    ) -> dict[str, Any]:
        env_fp = _env_fingerprint(env)
        started = time.monotonic()

        def emit(event: str, **data: Any) -> None:
            if self.events is None:
                return
            try:
                self.events.append_atomic(
                    event,
                    run_id=run_id,
                    agent_id=agent_id,
                    late_check=True,
                    candidate_id=candidate_id,
                    **data,
                )
            except Exception:
                pass

        def result(
            status: str,
            stage: str,
            *,
            classification: str | None = None,
            reason: str | None = None,
            evidence: dict[str, Any] | None = None,
            extra: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            payload: dict[str, Any] = {
                "status": status,
                "stage": stage,
                "candidate_id": candidate_id,
                "patch_hash": patch_hash,
                "base_commit": base_commit,
                "duration_seconds": round(time.monotonic() - started, 3),
            }
            if classification is not None:
                payload["classification"] = classification
            if reason is not None:
                payload["reason"] = reason
            if evidence is not None:
                payload["evidence"] = evidence
            if extra:
                payload.update(extra)
            excluded = {
                "status",
                "stage",
                "candidate_id",
                "patch_hash",
                "base_commit",
                "duration_seconds",
                "evidence",
                "classification",
                "reason",
            }
            emit(
                "verification_finished",
                status="passed" if status == "passed" else "failed",
                stage=stage,
                classification=classification,
                reason=reason,
                duration_seconds=payload["duration_seconds"],
                **{k: v for k, v in payload.items() if k not in excluded},
            )
            if status != "passed":
                emit(
                    "candidate.rejected",
                    stage=stage,
                    classification=classification,
                    reason=reason,
                    patch_hash=patch_hash,
                )
            return payload

        emit("verification_started", stage="snapshot", patch_path=str(patch_path))
        if self.cancel_event.is_set():
            return result(
                "cancelled",
                "snapshot",
                classification="infra",
                reason="cancelled",
            )

        # 1. The patch snapshot must match the hash captured by intake.
        try:
            patch_bytes = read_bounded(
                patch_path.resolve(strict=True), self.config.max_patch_bytes
            )
        except OSError as exc:
            return result(
                "rejected",
                "snapshot",
                classification="infra",
                reason=f"patch_unreadable:{exc}",
            )
        if len(patch_bytes) > self.config.max_patch_bytes:
            return result(
                "rejected",
                "quality_gate",
                classification="deterministic",
                reason="patch_too_large",
                extra={
                    "patch_bytes": len(patch_bytes),
                    "limit": self.config.max_patch_bytes,
                },
            )
        actual_hash = hashlib.sha256(patch_bytes).hexdigest()
        if actual_hash != patch_hash:
            return result(
                "rejected",
                "snapshot",
                classification="deterministic",
                reason="patch_hash_mismatch",
            )

        # 2. Measure the base commit once, in a disposable clone.
        if self.cancel_event.is_set():
            return result(
                "cancelled",
                "baseline",
                classification="infra",
                reason="cancelled",
            )
        full_cmd = list(self.config.full_suite_command)
        try:
            baseline = get_baseline(repo, base_commit, full_cmd, env_fp, self.sandbox)
            if baseline is None:
                baseline = build_baseline(
                    repo,
                    base_commit,
                    full_cmd,
                    env,
                    env_fp,
                    timeout=self.config.baseline_timeout_seconds,
                    sandbox=self.sandbox,
                    cancel_event=self.cancel_event,
                )
        except Exception as exc:
            return result(
                "rejected",
                "baseline",
                classification="infra",
                reason=f"baseline_failed:{type(exc).__name__}",
            )
        if not baseline_is_usable(baseline):
            return result(
                "rejected",
                "baseline",
                classification="infra",
                reason="baseline_unavailable",
            )
        baseline_failing_hash = _failing_hash(baseline.failing_tests)

        with tempfile.TemporaryDirectory(prefix="racegreen-vfy-") as td:
            checkout = Path(td) / "co"
            clone = self._git(
                ["clone", "--no-checkout", "--no-hardlinks", str(repo), str(checkout)]
            )
            if clone.returncode != 0:
                return result(
                    "rejected",
                    "clone",
                    classification="infra",
                    reason="git_clone_failed",
                )
            checked_out = self._git(["checkout", "--detach", base_commit], checkout)
            if checked_out.returncode != 0:
                return result(
                    "rejected",
                    "checkout",
                    classification="infra",
                    reason="git_checkout_failed",
                )
            head = self._git(["rev-parse", "HEAD"], checkout)
            if head.returncode != 0 or head.stdout.strip() != base_commit:
                return result(
                    "rejected",
                    "checkout",
                    classification="infra",
                    reason="base_commit_mismatch",
                )

            patch_abs = str(patch_path.resolve())
            checked = self._git(
                ["apply", "--check", "--whitespace=nowarn", patch_abs], checkout
            )
            if checked.returncode != 0:
                return result(
                    "rejected",
                    "apply",
                    classification="deterministic",
                    reason="patch_does_not_apply",
                )
            applied = self._git(
                ["apply", "--whitespace=nowarn", patch_abs], checkout
            )
            if applied.returncode != 0:
                return result(
                    "rejected",
                    "apply",
                    classification="deterministic",
                    reason="patch_apply_failed",
                )
            # The applied tree must be the intended mutation, and it must be
            # reversible so a partial or empty apply cannot masquerade as one.
            reverse = self._git(["apply", "--reverse", "--check", patch_abs], checkout)
            if reverse.returncode != 0:
                return result(
                    "rejected",
                    "apply",
                    classification="deterministic",
                    reason="patch_apply_mismatch",
                )
            diff = self._git(["diff", "--name-only", "HEAD"], checkout)
            if diff.returncode != 0:
                return result(
                    "rejected",
                    "apply",
                    classification="infra",
                    reason="unable_to_inspect_patch",
                )
            applied_paths = tuple(
                line for line in diff.stdout.splitlines() if line.strip()
            )
            if not applied_paths:
                return result(
                    "rejected",
                    "apply",
                    classification="deterministic",
                    reason="empty_patch",
                )
            # The applied patch is authoritative. A stale caller-provided path
            # list must never hide files that the patch actually changes.
            effective_paths = applied_paths or changed_paths
            changed_files = len(effective_paths)
            changed_lines = _patch_changed_lines(patch_bytes)
            if (
                self.config.max_changed_files is not None
                and changed_files > self.config.max_changed_files
            ):
                return result(
                    "rejected",
                    "quality_gate",
                    classification="deterministic",
                    reason="too_many_changed_files",
                    extra={
                        "changed_files": changed_files,
                        "limit": self.config.max_changed_files,
                    },
                )
            if (
                self.config.max_changed_lines is not None
                and changed_lines > self.config.max_changed_lines
            ):
                return result(
                    "rejected",
                    "quality_gate",
                    classification="deterministic",
                    reason="too_many_changed_lines",
                    extra={
                        "changed_lines": changed_lines,
                        "limit": self.config.max_changed_lines,
                    },
                )

            # 3. Optional conservative selection. It can reject early, but it
            #    can never declare a winner.
            target_cmd = list(self.config.test_command or [])
            target_tests_passed = False
            selection = self.selector.select(effective_paths)
            if target_cmd and not selection.use_full_suite and selection.selected_tests:
                junit_sel = checkout / ".racegreen-selected-junit.xml"
                _prepare_output(junit_sel)
                cmd_sel = with_junit_output(
                    target_cmd, Path(junit_sel.name)
                ) + list(selection.selected_tests)
                rc_sel, out_sel, err_sel, dur_sel = self._run_cmd(
                    cmd_sel,
                    str(checkout),
                    env,
                    self.config.timeout_selected_seconds or 300.0,
                )
                if rc_sel != 0:
                    cls = classify(rc_sel, out_sel, err_sel)
                    if self.retry.should_retry(cls.value, 0):
                        rc_sel, out_sel, err_sel, dur_sel = self._run_cmd(
                            cmd_sel,
                            str(checkout),
                            env,
                            self.config.timeout_selected_seconds or 300.0,
                        )
                    if rc_sel != 0:
                        return result(
                            "rejected",
                            "selected",
                            classification=classify(rc_sel, out_sel, err_sel).value,
                            reason="selected_tests_failed",
                        )
                target_tests_passed = True
                emit(
                    "verification_finished",
                    status="passed",
                    stage="selected",
                    duration_seconds=round(dur_sel, 3),
                    selection_method=selection.selection_method,
                )
                emit("candidate.confirming", state="confirming", patch_hash=patch_hash)
            elif baseline.failing_tests and target_cmd and target_cmd != full_cmd:
                # A configured target-test command is the F2P signal when the
                # base commit already has unrelated failures. It cannot
                # declare a winner by itself; the full suite below still must
                # retain no new failures.
                junit_target = checkout / ".racegreen-target-junit.xml"
                _prepare_output(junit_target)
                cmd_target = with_junit_output(target_cmd, Path(junit_target.name))
                rc_target, out_target, err_target, dur_target = self._run_cmd(
                    cmd_target,
                    str(checkout),
                    env,
                    self.config.timeout_selected_seconds or 300.0,
                )
                if rc_target != 0:
                    cls = classify(rc_target, out_target, err_target)
                    if self.retry.should_retry(cls.value, 0):
                        rc_target, out_target, err_target, dur_target = self._run_cmd(
                            cmd_target,
                            str(checkout),
                            env,
                            self.config.timeout_selected_seconds or 300.0,
                        )
                    if rc_target != 0:
                        return result(
                            "rejected",
                            "target",
                            classification=classify(
                                rc_target, out_target, err_target
                            ).value,
                            reason="target_tests_failed",
                        )
                target_tests_passed = True
                emit(
                    "verification_finished",
                    status="passed",
                    stage="target",
                    duration_seconds=round(dur_target, 3),
                )
                emit("candidate.confirming", state="confirming", patch_hash=patch_hash)

            # 4. Full-suite confirmation with baseline comparison.
            timeout_full = self.config.timeout_full_seconds or 1800.0
            attempt = 0
            earlier: list[str] = []
            while True:
                if not self.budget.try_acquire_full_suite():
                    return result(
                        "rejected",
                        "full",
                        classification="infra",
                        reason="full_suite_budget_exhausted",
                    )
                junit_full = checkout / ".racegreen-full-junit.xml"
                _prepare_output(junit_full)
                cmd_full = with_junit_output(full_cmd, Path(junit_full.name))
                emit("candidate.confirming", state="confirming", patch_hash=patch_hash)
                rc, out, err, duration = self._run_cmd(
                    cmd_full, str(checkout), env, timeout_full
                )
                summary = parse_junit(junit_full) if junit_full.exists() else JUnitSummary()
                case_count = _case_count(summary)
                failing = _failing_tests(summary)
                new_failures = failing - baseline.failing_tests
                removed_failures = baseline.failing_tests - failing
                junit_bytes = b""
                if junit_full.exists():
                    try:
                        junit_bytes = read_bounded(
                            junit_full, self.config.artifact_max_bytes
                        )
                    except OSError:
                        junit_bytes = b""
                artifact_hash = write_bounded(
                    Path(td) / "full-junit-artifact.xml",
                    junit_bytes,
                    self.config.artifact_max_bytes,
                )

                if rc == 5 or (junit_full.exists() and case_count == 0):
                    return result(
                        "rejected",
                        "full",
                        classification="infra",
                        reason="no_tests_collected",
                    )
                if not junit_full.exists():
                    return result(
                        "rejected",
                        "full",
                        classification="infra",
                        reason="junit_missing",
                    )

                suite_clean = (
                    rc == 0
                    and not new_failures
                    and (summary.failures + summary.errors) == 0
                )
                baseline_failures_retained = (
                    target_tests_passed
                    and rc != 0
                    and bool(baseline.failing_tests)
                    and bool(failing)
                    and not new_failures
                    and failing.issubset(baseline.failing_tests)
                    and (summary.failures + summary.errors) > 0
                )
                if suite_clean or baseline_failures_retained:
                    evidence = VerificationEvidence(
                        candidate_id=candidate_id,
                        patch_hash=patch_hash,
                        base_commit=base_commit,
                        argv=tuple(cmd_full),
                        env_fingerprint=env_fp,
                        junit_artifact_hash=artifact_hash,
                        attempt_number=attempt,
                        earlier_failures=tuple(earlier),
                        baseline_failing_set_hash=baseline_failing_hash,
                        complete_full_suite=True,
                    )
                    return result(
                        "passed",
                        "full",
                        evidence=evidence.__dict__,
                        extra={
                            "junit": {
                                "tests": case_count,
                                "failures": summary.failures,
                                "errors": summary.errors,
                                "skipped": summary.skipped,
                                "passed": summary.passed,
                            },
                            "failing_tests": sorted(failing),
                            "new_failures": sorted(new_failures),
                            "removed_failures": sorted(removed_failures),
                            "baseline_failing_tests": sorted(baseline.failing_tests),
                            "baseline_failing_set_hash": baseline_failing_hash,
                            "target_tests_passed": target_tests_passed,
                            "baseline_failures_retained": baseline_failures_retained,
                            "full_suite_runs": self.budget.used,
                            "duration_seconds": round(duration, 3),
                            "changed_files": changed_files,
                            "changed_lines": changed_lines,
                        },
                    )

                classification = classify(rc, out, err)
                if rc == 0:
                    # Exit 0 but JUnit reports failures: never confirm.
                    classification = FailureClass.INFRA
                if self.retry.should_retry(classification.value, attempt):
                    earlier.append(classification.value)
                    attempt += 1
                    continue
                return result(
                    "rejected",
                    "full",
                    classification=classification.value,
                    reason="new_failures" if new_failures else "full_suite_failed",
                    extra={
                        "junit": {
                            "tests": case_count,
                            "failures": summary.failures,
                            "errors": summary.errors,
                            "skipped": summary.skipped,
                            "passed": summary.passed,
                        },
                        "failing_tests": sorted(failing),
                        "new_failures": sorted(new_failures),
                        "removed_failures": sorted(removed_failures),
                        "baseline_failing_tests": sorted(baseline.failing_tests),
                        "baseline_failing_set_hash": baseline_failing_hash,
                        "full_suite_runs": self.budget.used,
                        "duration_seconds": round(duration, 3),
                    },
                )
