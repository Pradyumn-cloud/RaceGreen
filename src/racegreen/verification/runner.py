"""Staged verification runner."""

from __future__ import annotations

import subprocess
import tempfile
import time
from pathlib import Path

from ..agent.events import EventStore
from .artifacts import write_bounded
from .baseline import build_baseline, get_baseline
from .classifier import classify
from .config import VerificationConfig
from .env import alloc_port, free_port, verification_env
from .evidence import VerificationEvidence
from .junit import parse_junit
from .retry import RetryPolicy
from .selection import TestSelector
from .winner import WinnerSlot


class VerificationRunner:
    def __init__(self, events: EventStore | None = None, config: VerificationConfig | None = None, winner_slot: WinnerSlot | None = None) -> None:
        self.events = events
        self.config = config or VerificationConfig()
        self.selector = TestSelector(self.config.use_selection)
        self.retry = RetryPolicy(self.config.max_retries)
        self.winner_slot = winner_slot or WinnerSlot()

    def _run_cmd(self, cmd: list[str], cwd: str, env: dict[str, str], timeout: float) -> tuple[int, str, str, float]:
        start = time.monotonic()
        try:
            proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, timeout=timeout)
            out = proc.stdout.decode("utf-8", errors="replace")
            err = proc.stderr.decode("utf-8", errors="replace")
            return proc.returncode, out, err, time.monotonic() - start
        except subprocess.TimeoutExpired as e:
            out = (e.stdout or b"").decode("utf-8", errors="replace")
            err = (e.stderr or b"").decode("utf-8", errors="replace")
            return -9, out, err + "\ntimeout", time.monotonic() - start

    def verify_candidate(self, repo: Path, worktree: Path, candidate_id: str, patch_path: Path, patch_hash: str, base_commit: str, run_id: str, agent_id: str, changed_paths: tuple[str, ...] = ()) -> dict:
        env = verification_env()
        port = alloc_port()
        try:
            get_baseline(repo, base_commit, self.config.full_suite_command) or build_baseline(
                repo, base_commit, self.config.full_suite_command, env
            )
            # apply patch in temp checkout
            with tempfile.TemporaryDirectory(prefix="racegreen-vfy-") as td:
                checkout = Path(td) / "co"
                subprocess.run(["git", "clone", "--no-hardlinks", str(repo), str(checkout)], check=False, capture_output=True)
                subprocess.run(["git", "-C", str(checkout), "checkout", "--detach", base_commit], check=False, capture_output=True)
                subprocess.run(["git", "-C", str(checkout), "apply", "--whitespace=nowarn", str(patch_path)], check=False, capture_output=True)
                cwd = str(checkout)
                # selection
                sel = self.selector.select(changed_paths)
                timeout_sel = self.config.timeout_selected_seconds or (300.0)
                timeout_full = self.config.timeout_full_seconds or (1800.0)
                # selected
                if sel.use_full_suite is False and sel.selected_tests:
                    junit_sel = Path(td) / "sel-junit.xml"
                    cmd = list(self.config.test_command) + [f"--junitxml={junit_sel}"] + list(sel.selected_tests)
                    rc, out, err, dur = self._run_cmd(cmd, cwd, env, timeout_sel)
                    if rc == 0:
                        # queue full confirmation
                        pass
                    else:
                        cls = classify(rc, out, err)
                        if self.retry.should_retry(cls.value, 0):
                            rc2, out2, err2, dur2 = self._run_cmd(cmd, cwd, env, timeout_sel)
                            rc, out, err = rc2, out2, err2
                        else:
                            return {"status": "rejected", "stage": "selected", "classification": cls.value}
                # full suite
                junit_full = Path(td) / "full-junit.xml"
                cmd_full = list(self.config.full_suite_command) + [f"--junitxml={junit_full}"]
                attempt = 0
                while attempt <= self.config.max_retries:
                    rc, out, err, dur = self._run_cmd(cmd_full, cwd, env, timeout_full)
                    if rc == 5:  # no tests collected
                        return {"status": "rejected", "stage": "full", "classification": "infra"}
                    if rc == 0:
                        parse_junit(junit_full)
                        # base-aware check
                        # simple: if no new failures beyond base? better check cases
                        evidence = VerificationEvidence(
                            candidate_id=candidate_id,
                            patch_hash=patch_hash,
                            base_commit=base_commit,
                            argv=tuple(cmd_full),
                            env_fingerprint="",
                            junit_artifact_hash=write_bounded(Path(td) / "full-junit-artifact.xml", junit_full.read_bytes() if junit_full.exists() else b"", self.config.artifact_max_bytes),
                            attempt_number=attempt,
                            earlier_failures=tuple(),
                            baseline_failing_set_hash="",
                            complete_full_suite=True,
                        )
                        # atomic CAS
                        if self.winner_slot.try_set_winner({"candidate_id": candidate_id, "patch_hash": patch_hash, "evidence": evidence.__dict__}):
                            if self.events:
                                try:
                                    self.events.append_atomic("candidate.won", run_id=run_id, agent_id=agent_id, late_check=True, candidate_id=candidate_id, patch_hash=patch_hash)
                                except Exception:
                                    pass
                            return {"status": "won", "stage": "full", "evidence": evidence.__dict__}
                        else:
                            if self.events:
                                try:
                                    self.events.append_atomic("candidate.confirmed_but_not_selected", run_id=run_id, agent_id=agent_id, late_check=True, candidate_id=candidate_id, patch_hash=patch_hash)
                                except Exception:
                                    pass
                            return {"status": "confirmed_but_not_selected", "stage": "full"}
                    else:
                        cls = classify(rc, out, err)
                        if self.retry.should_retry(cls.value, attempt):
                            attempt += 1
                            continue
                        if self.events:
                            try:
                                self.events.append_atomic("candidate.rejected", run_id=run_id, agent_id=agent_id, late_check=True, candidate_id=candidate_id, patch_hash=patch_hash, classification=cls.value, stage="full")
                            except Exception:
                                pass
                        return {"status": "rejected", "stage": "full", "classification": cls.value}
                return {"status": "rejected", "stage": "full", "classification": "infra"}
        finally:
            free_port(port)
