"""Verification configuration with argument arrays."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VerificationConfig:
    test_command: list[str] | None = None
    full_suite_command: list[str] = None  # type: ignore
    use_selection: bool = True
    use_prefilter: bool = True
    selection_strategy: str = "conservative"
    timeout_selected_seconds: float | None = None
    timeout_full_seconds: float | None = None
    base_timeout_multiplier: float = 5.0
    max_retries: int = 2
    artifact_max_bytes: int = 262144
    max_parallel_verifications: int = 2
    max_full_suite_runs_per_run: int | None = 10
    speculative_full_suite: bool = False
    audit_mode: bool = False
    baseline_timeout_seconds: float = 1800.0
    max_changed_files: int | None = None
    max_changed_lines: int | None = None
    max_patch_bytes: int = 5 * 1024 * 1024
    sandbox: str = "none"
    sandbox_image: str | None = None
    sandbox_network: str = "none"
    sandbox_memory: str | None = None
    sandbox_cpus: str | None = None

    def __post_init__(self) -> None:
        if self.full_suite_command is None:
            object.__setattr__(self, "full_suite_command", ["pytest", "-q"])
        if self.test_command is None:
            object.__setattr__(self, "test_command", list(self.full_suite_command))
