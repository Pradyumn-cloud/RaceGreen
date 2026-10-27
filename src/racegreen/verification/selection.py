"""Conservative test selection with fallbacks."""

from __future__ import annotations

from dataclasses import dataclass


FALLBACK_TRIGGERS = (
    "non_py",
    "conftest",
    "pytest_config",
    "tox_config",
    "setup_cfg",
    "pyproject_pytest",
    "deps",
    "deleted_or_new_unmapped",
    "generated",
    "testmon_missing_or_stale",
)


@dataclass
class SelectionResult:
    use_full_suite: bool
    selected_tests: tuple[str, ...] = ()
    selection_method: str = "none"
    miss_reason: str | None = None


class TestSelector:
    def __init__(self, use_selection: bool = True) -> None:
        self.use_selection = use_selection

    def select(self, changed_paths: tuple[str, ...], base_duration_s: float | None = None, full_duration_s: float | None = None) -> SelectionResult:
        if not self.use_selection:
            return SelectionResult(True, (), "disabled", "disabled")
        # check fallback triggers
        for p in changed_paths:
            if not p.endswith(".py"):
                return SelectionResult(True, (), "fallback", "non_py")
            if p.endswith("conftest.py"):
                return SelectionResult(True, (), "fallback", "conftest")
            if p in ("pytest.ini", "tox.ini", "setup.cfg"):
                return SelectionResult(True, (), "fallback", "pytest_config")
            if p in ("pyproject.toml", "requirements.txt", "requirements-dev.txt", "Pipfile", "Pipfile.lock", "poetry.lock", "uv.lock"):
                return SelectionResult(True, (), "fallback", "deps")
        # cost gating
        if base_duration_s is not None and full_duration_s is not None:
            if base_duration_s >= full_duration_s * 0.5:  # estimated cost large
                return SelectionResult(True, (), "fallback", "cost_gate")
        # conservative: default to fallback unless strong mapping exists
        return SelectionResult(True, (), "fallback", "unknown_mapping")
