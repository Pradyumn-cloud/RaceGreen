from __future__ import annotations

from pathlib import Path

import pytest
from test_agent import _repo

from racegreen.agent.candidate import (
    CandidateVerificationError,
    snapshot_candidate,
    verify_candidate_snapshot,
)


def test_candidate_snapshot_is_verified_against_base(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    candidate = snapshot_candidate(
        repo, tmp_path / "candidate.patch", candidate_id="candidate-1", base_commit=base
    )
    verify_candidate_snapshot(repo, candidate)


def test_candidate_hash_tampering_is_rejected(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    candidate = snapshot_candidate(
        repo, tmp_path / "candidate.patch", candidate_id="candidate-1", base_commit=base
    )
    candidate.patch_path.write_bytes(candidate.patch_path.read_bytes() + b"\n")
    with pytest.raises(CandidateVerificationError, match="hash"):
        verify_candidate_snapshot(repo, candidate)
