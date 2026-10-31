import json
from pathlib import Path

from racegreen.report import write_report
from racegreen.storage.db import RaceDB
from racegreen.storage.security import authorize_artifact, safe_terminal_text


def test_events_are_sequenced_and_projected(tmp_path):
    db = RaceDB(tmp_path / "trace.db")
    db.append_event(
        {
            "event": "run_started",
            "run_id": "run-1",
            "agent_id": "scheduler",
            "timestamp": "2026-01-01T00:00:00Z",
        }
    )
    db.append_event(
        {
            "event": "agent_started",
            "run_id": "run-1",
            "agent_id": "agent-1",
            "timestamp": "2026-01-01T00:00:01Z",
        }
    )
    snapshot = db.run_snapshot("run-1")
    assert snapshot["event_count"] == 2
    assert snapshot["agents"][0]["status"] == "working"
    assert [event["sequence"] for event in db.events("run-1")] == [1, 2]


def test_payloads_are_bounded_and_terminal_text_is_safe(tmp_path):
    db = RaceDB(tmp_path / "trace.db")
    record = {
        "event": "output",
        "run_id": "run-1",
        "agent_id": "agent-1",
        "timestamp": "now",
        "output": "x" * 100_000,
    }
    stored = db.append_event(record)
    assert stored["truncated"] is True
    assert len(json.dumps(stored).encode()) <= RaceDB.EVENT_LIMIT
    assert safe_terminal_text("\x1b]0;bad\x07\x1b[31mred") == "red"


def test_artifact_authority_rejects_escape(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    artifact = root / "patch.diff"
    artifact.write_text("patch", encoding="utf-8")
    assert authorize_artifact(root, Path("patch.diff")) == artifact.resolve()

    import pytest

    with pytest.raises(ValueError):
        authorize_artifact(root, Path("../outside"))


def test_report_is_self_contained_and_escaped(tmp_path):
    db = RaceDB(tmp_path / "trace.db")
    db.append_event(
        {
            "event": "run_started",
            "run_id": "run-report",
            "agent_id": "scheduler",
            "timestamp": "now",
            "output": "<script>alert(1)</script>",
        }
    )
    report = write_report(db, "run-report", tmp_path / "report.html")
    content = report.read_text(encoding="utf-8")
    assert "&lt;script&gt;" in content
    assert "<script>alert(1)</script>" not in content
    assert "default-src 'none'" in content
