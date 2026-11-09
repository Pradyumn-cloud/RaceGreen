from pathlib import Path

from test_agent import _repo

import racegreen.verification.intake as intake_module
from racegreen.verification.intake import CandidateIntake


def test_intake_rejects_unstable_snapshot(tmp_path: Path, monkeypatch) -> None:
    repo, base = _repo(tmp_path)
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")

    def fail_verification(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("candidate patch hash changed")

    monkeypatch.setattr(intake_module, "verify_candidate_snapshot", fail_verification)
    intake = CandidateIntake()

    result = intake.freeze_and_intake(
        repo,
        tmp_path / "candidates",
        "candidate-1",
        base,
        "run-1",
        "agent-1",
    )

    assert result.accepted is False
    assert result.snapshot is None
    assert result.rejections[0].rule == "snapshot_verification_failed"


def test_intake_allocates_unique_ids_for_repeated_candidate_ids(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    intake = CandidateIntake()

    first = intake.freeze_and_intake(
        repo,
        tmp_path / "candidates",
        "candidate-1",
        base,
        "run-1",
        "agent-1",
    )
    second = intake.freeze_and_intake(
        repo,
        tmp_path / "candidates",
        "candidate-1",
        base,
        "run-1",
        "agent-2",
    )

    assert first.accepted is True
    assert second.accepted is True
    assert first.snapshot is not None
    assert second.snapshot is not None
    assert first.snapshot.candidate_id == "candidate-1"
    assert second.snapshot.candidate_id == "agent-2:candidate-1"


def test_disabled_prefilter_still_rejects_test_file_changes(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    (repo / "test_app.py").write_text("def test_added(): pass\n", encoding="utf-8")
    intake = CandidateIntake(enabled=False)

    result = intake.freeze_and_intake(
        repo,
        tmp_path / "candidates",
        "candidate-1",
        base,
        "run-1",
        "agent-1",
    )

    assert result.accepted is False
    assert result.rejections[0].rule == "test_file_change"


def test_intake_rejects_empty_patch(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    intake = CandidateIntake()

    result = intake.freeze_and_intake(
        repo,
        tmp_path / "candidates",
        "candidate-1",
        base,
        "run-1",
        "agent-1",
    )

    assert result.accepted is False
    assert result.snapshot is None
    assert result.rejections[0].rule == "empty_patch"


def test_intake_sanitizes_unsafe_candidate_ids(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    candidate_dir = tmp_path / "candidates"
    intake = CandidateIntake()

    result = intake.freeze_and_intake(
        repo,
        candidate_dir,
        "../escape",
        base,
        "run-1",
        "agent-1",
    )

    assert result.accepted is True
    assert result.snapshot is not None
    assert result.snapshot.patch_path.parent == candidate_dir.resolve()
    assert ".." not in result.snapshot.patch_path.name
    assert not (tmp_path / "escape.patch").exists()
