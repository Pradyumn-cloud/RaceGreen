"""Verification configuration with argument arrays."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VerificationConfig:
    test_command: list[str] | None = None
    full_suite_command: list[str] = None  # type: ignore
    use_selection: bool = True
    selection_strategy: str = "conservative"
    timeout_selected_seconds: float | None = None
    timeout_full_seconds: float | None = None
    base_timeout_multiplier: float = 5.0
    max_retries: int = 2
    artifact_max_bytes: int = 262144
    max_parallel_verifications: int = 2
    max_full_suite_runs_per_run: int = 1
    speculative_full_suite: bool = False
    audit_mode: bool = False

    def __post_init__(self):
        if self.full_suite_command is None:
            object.__setattr__(self, "full_suite_command", ["pytest", "-q"])
        if self.test_command is None:
            object.__setattr__(self, "test_command", list(self.full_suite_command))
