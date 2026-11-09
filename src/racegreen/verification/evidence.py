"""Typed verification evidence."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VerificationEvidence:
    candidate_id: str
    patch_hash: str
    base_commit: str
    argv: tuple[str, ...]
    env_fingerprint: str
    junit_artifact_hash: str
    attempt_number: int
    earlier_failures: tuple[str, ...]
    baseline_failing_set_hash: str = ""
    complete_full_suite: bool = False
