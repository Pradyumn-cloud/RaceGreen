from __future__ import annotations

import random
from pathlib import Path

from racegreen.storage.db import RaceDB
from racegreen.storage.security import authorize_artifact


def test_artifact_authority_rejects_traversal_and_symlink_like_paths(tmp_path: Path) -> None:
    root = tmp_path / "run"
    root.mkdir()
    (root / "safe.txt").write_text("ok", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    rejected = [Path("..") / "outside.txt", outside]
    for path in rejected:
        try:
            authorize_artifact(root, path)
        except ValueError:
            pass
        else:
            raise AssertionError(f"escaped path was accepted: {path}")
    assert authorize_artifact(root, Path("safe.txt")) == (root / "safe.txt").resolve()
    assert outside.read_text(encoding="utf-8") == "secret"


def test_event_sequences_remain_contiguous_under_repeated_writes(tmp_path: Path) -> None:
    db = RaceDB(tmp_path / "trace.db")
    records = list(range(50))
    random.Random(0).shuffle(records)
    for value in records:
        db.append_event(
            {
                "event": "agent_status",
                "run_id": "run-repeated",
                "agent_id": f"agent-{value % 3}",
                "status": "working",
                "timestamp": str(value),
            }
        )

    events = db.events("run-repeated")
    assert [event["sequence"] for event in events] == list(range(1, 51))
    verification = db.verify()
    assert verification["integrity"] == "ok"
    assert verification["violations"] == []


def test_winner_write_is_idempotent_and_preserves_single_winner(tmp_path: Path) -> None:
    db = RaceDB(tmp_path / "trace.db")
    db.append_event({"event": "run_started", "run_id": "run-winner", "timestamp": "now"})
    assert db.try_set_winner("run-winner", "candidate-1", "hash-1", "base", {"full": True})
    assert not db.try_set_winner("run-winner", "candidate-2", "hash-2", "base", {"full": True})
    assert db.get_winner("run-winner")["candidate_id"] == "candidate-1"
    assert db.verify()["violations"] == []
