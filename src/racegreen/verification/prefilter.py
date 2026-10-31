"""Pre-filter engine with two-tier limits and rules."""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .gitutil import apply_check, numstat, summary
from .syntax import check_python_syntax

DEFAULT_EXCLUDES = (
    "__pycache__/",
    "*.pyc",
    "*.pyo",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    "*.egg-info/",
    ".coverage",
    ".venv/",
    "venv/",
    "env/",
    ".tox/",
)


TEST_CONFIG_FILES = {
    "conftest.py",
    "pytest.ini",
    "tox.ini",
    "setup.cfg",
}


@dataclass
class PreFilterConfig:
    max_patch_bytes_soft: int = 1_000_000
    max_files_soft: int = 200
    max_patch_bytes_hard: int = 10_000_000
    allow_binaries: bool = False
    allow_large_refactors: bool = False
    excludes: tuple[str, ...] = DEFAULT_EXCLUDES
    target_python_version: tuple[int, int] = (3, 12)
    syntax_check_max_bytes: int = 1_000_000


@dataclass
class PreFilterRejection:
    rule: str
    evidence: str
    patch_hash: str
    candidate_id: str


@dataclass
class PreFilterResult:
    accepted: bool
    rejections: tuple[PreFilterRejection, ...] = ()
    soft_limit_triggered: bool = False
    hard_cap_triggered: bool = False
    excluded_paths_count: int = 0
    duration_ms: float = 0.0
    changed_paths: tuple[str, ...] = ()


class PreFilter:
    def __init__(self, config: PreFilterConfig | None = None) -> None:
        self.config = config or PreFilterConfig()

    def filter(self, patch_bytes: bytes, patch_hash: str, candidate_id: str, worktree: Path | None = None, base_commit: str | None = None) -> PreFilterResult:
        start = time.monotonic()
        rejections: list[PreFilterRejection] = []
        soft_triggered = False
        hard_triggered = False

        if not patch_bytes or patch_bytes.strip() == b"":
            rejections.append(PreFilterRejection("empty_diff", "empty patch", patch_hash, candidate_id))
            return PreFilterResult(False, tuple(rejections), duration_ms=(time.monotonic() - start) * 1000)

        if len(patch_bytes) > self.config.max_patch_bytes_hard and not self.config.allow_large_refactors:
            rejections.append(PreFilterRejection("hard_size_cap", f"patch exceeds hard cap {len(patch_bytes)} bytes", patch_hash, candidate_id))
            hard_triggered = True
            return PreFilterResult(False, tuple(rejections), False, True, duration_ms=(time.monotonic() - start) * 1000)

        if len(patch_bytes) > self.config.max_patch_bytes_soft or self._count_files(patch_bytes) > self.config.max_files_soft:
            soft_triggered = True

        ok_apply, err_apply = apply_check(patch_bytes, worktree, reverse=worktree is not None)
        if not ok_apply:
            rejections.append(PreFilterRejection("apply_check_failed", err_apply[:200], patch_hash, candidate_id))

        nst = numstat(patch_bytes)
        summ = summary(patch_bytes)
        changed = self._extract_paths(nst)
        if "120000" in summ or "mode 120000" in summ:
            rejections.append(PreFilterRejection("symlink_detected", summ[:200], patch_hash, candidate_id))
        if "160000" in summ or "mode 160000" in summ:
            rejections.append(PreFilterRejection("submodule_detected", summ[:200], patch_hash, candidate_id))
        if b"\x00" in patch_bytes and not self.config.allow_binaries:
            if re.search(r"^- -\s+", nst, flags=re.MULTILINE):
                rejections.append(PreFilterRejection("binary_artifact", "binary file change detected", patch_hash, candidate_id))
        for p in changed:
            name = os.path.basename(p)
            if name in TEST_CONFIG_FILES:
                rejections.append(PreFilterRejection("test_config_tamper", f"edit to {p}", patch_hash, candidate_id))
                break
            if name in {".gitattributes", ".gitmodules"}:
                rejections.append(PreFilterRejection("vcs_metadata_change", f"edit to {p}", patch_hash, candidate_id))
                break

        if worktree is not None and base_commit is not None:
            syn = check_python_syntax(patch_bytes, worktree, base_commit, self.config.target_python_version, self.config.syntax_check_max_bytes)
            if not syn.ok and syn.error:
                rejections.append(PreFilterRejection("python_syntax_error_introduced", syn.error, patch_hash, candidate_id))

        accepted = len(rejections) == 0
        return PreFilterResult(
            accepted=accepted,
            rejections=tuple(rejections),
            soft_limit_triggered=soft_triggered,
            hard_cap_triggered=hard_triggered,
            duration_ms=(time.monotonic() - start) * 1000,
            changed_paths=tuple(changed),
        )

    def _count_files(self, patch_bytes: bytes) -> int:
        nst = numstat(patch_bytes)
        return len([line for line in nst.splitlines() if line.strip()])

    def _extract_paths(self, nst: str) -> tuple[str, ...]:
        paths = []
        for line in nst.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) >= 3:
                p = parts[2]
                if p == "-":
                    continue
                paths.append(p)
            elif len(parts) == 2:
                paths.append(parts[1])
        return tuple(paths)
