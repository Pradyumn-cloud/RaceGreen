import json
import sqlite3
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


def _append_lifecycle(db: RaceDB, terminal: bool = True) -> None:
    events = [
        {
            "event": "run_configured",
            "run_id": "run-life",
            "agent_id": "scheduler",
            "timestamp": "2026-01-01T00:00:00Z",
            "base_commit": "abc123",
            "config_fingerprint": "cfg-1",
        },
        {
            "event": "run_started",
            "run_id": "run-life",
            "agent_id": "scheduler",
            "timestamp": "2026-01-01T00:00:01Z",
        },
        {
            "event": "agent_started",
            "run_id": "run-life",
            "agent_id": "agent-1",
            "timestamp": "2026-01-01T00:00:02Z",
        },
        {
            "event": "candidate_frozen",
            "run_id": "run-life",
            "agent_id": "agent-1",
            "candidate_id": "candidate-1",
            "timestamp": "2026-01-01T00:00:03Z",
        },
        {
            "event": "prefilter_finished",
            "run_id": "run-life",
            "agent_id": "agent-1",
            "candidate_id": "candidate-1",
            "timestamp": "2026-01-01T00:00:04Z",
            "patch_hash": "hash-1",
            "patch_path": "/tmp/candidate-1.patch",
            "accepted": True,
        },
        {
            "event": "agent_finished",
            "run_id": "run-life",
            "agent_id": "agent-1",
            "timestamp": "2026-01-01T00:00:05Z",
            "status": "submitted",
        },
    ]
    if terminal:
        events.append(
            {
                "event": "run_finished",
                "run_id": "run-life",
                "agent_id": "scheduler",
                "timestamp": "2026-01-01T00:00:06Z",
                "state": "completed",
            }
        )
    for event in events:
        db.append_event(event)


def test_run_agent_and_candidate_projections_track_events(tmp_path):
    db = RaceDB(tmp_path / "trace.db")
    _append_lifecycle(db)
    snapshot = db.run_snapshot("run-life")
    assert snapshot["run"]["state"] == "completed"
    assert snapshot["run"]["base_commit"] == "abc123"
    assert snapshot["run"]["config_fingerprint"] == "cfg-1"
    assert snapshot["run"]["started_at"] == "2026-01-01T00:00:01Z"
    assert snapshot["run"]["finished_at"] == "2026-01-01T00:00:06Z"
    assert snapshot["run"]["last_event_sequence"] == 7
    assert snapshot["agents"][0]["status"] == "submitted"
    assert snapshot["candidates"][0]["state"] == "prefilter_passed"
    assert snapshot["candidates"][0]["last_event_type"] == "prefilter_finished"
    assert snapshot["candidates"][0]["has_patch"] is True
    assert "patch_path" not in snapshot["candidates"][0]
    overview = db.overview()
    assert overview["runs"]["total"] == 1
    assert overview["runs"]["completed"] == 1
    assert overview["candidates"]["by_state"]["prefilter_passed"] == 1
    assert overview["events"] == 7


def test_projection_rebuild_replays_events_and_preserves_winner(tmp_path):
    db = RaceDB(tmp_path / "trace.db")
    _append_lifecycle(db, terminal=False)
    assert db.try_set_winner(
        "run-life",
        "candidate-1",
        "hash-1",
        "abc123",
        {"complete_full_suite": True, "attempt_number": 1},
    )
    db.append_event(
        {
            "event": "run_finished",
            "run_id": "run-life",
            "agent_id": "scheduler",
            "timestamp": "2026-01-01T00:00:07Z",
            "state": "completed",
        }
    )
    with db._connect() as conn:
        conn.execute("UPDATE runs SET state='UNKNOWN', base_commit=NULL")
        conn.execute("DELETE FROM agents")
        conn.execute("DELETE FROM candidates")
        conn.commit()
    counts = db.rebuild_projections()
    assert counts == {"events": 8, "runs": 1, "agents": 1, "candidates": 1}
    snapshot = db.run_snapshot("run-life")
    assert snapshot["run"]["state"] == "completed"
    assert snapshot["run"]["base_commit"] == "abc123"
    assert snapshot["candidates"][0]["state"] == "won"
    assert snapshot["winner"]["candidate_id"] == "candidate-1"
    assert snapshot["winner"]["evidence_complete"] is True
    assert db.get_winner("run-life")["evidence"]["attempt_number"] == 1


def test_terminal_run_state_does_not_regress(tmp_path):
    db = RaceDB(tmp_path / "trace.db")
    _append_lifecycle(db)
    db.append_event(
        {
            "event": "run_started",
            "run_id": "run-life",
            "agent_id": "scheduler",
            "timestamp": "2026-01-01T00:00:08Z",
        }
    )
    db.append_event(
        {
            "event": "agent_status",
            "run_id": "run-life",
            "agent_id": "agent-1",
            "timestamp": "2026-01-01T00:00:09Z",
            "status": "working",
        }
    )
    snapshot = db.run_snapshot("run-life")
    assert snapshot["run"]["state"] == "completed"
    assert snapshot["agents"][0]["status"] == "submitted"


def test_candidate_rejection_and_verification_states(tmp_path):
    db = RaceDB(tmp_path / "trace.db")
    db.append_event(
        {
            "event": "run_started",
            "run_id": "run-candidate",
            "agent_id": "scheduler",
            "timestamp": "2026-01-01T00:00:00Z",
        }
    )
    db.append_event(
        {
            "event": "prefilter_finished",
            "run_id": "run-candidate",
            "agent_id": "agent-1",
            "candidate_id": "candidate-rejected",
            "timestamp": "2026-01-01T00:00:01Z",
            "patch_hash": "hash-bad",
            "accepted": False,
        }
    )
    db.append_event(
        {
            "event": "candidate_rejected",
            "run_id": "run-candidate",
            "agent_id": "agent-1",
            "candidate_id": "candidate-rejected",
            "timestamp": "2026-01-01T00:00:02Z",
            "rule": "binary_or_large",
            "evidence": "binary file",
        }
    )
    db.append_event(
        {
            "event": "verification_finished",
            "run_id": "run-candidate",
            "agent_id": "verifier",
            "candidate_id": "candidate-confirming",
            "timestamp": "2026-01-01T00:00:03Z",
            "state": "confirming",
            "stage": "full_suite",
        }
    )
    snapshot = db.run_snapshot("run-candidate")
    candidates = {item["candidate_id"]: item for item in snapshot["candidates"]}
    assert candidates["candidate-rejected"]["state"] == "rejected"
    assert candidates["candidate-rejected"]["classification"] is None
    assert candidates["candidate-rejected"]["reason"] == "binary file"
    assert candidates["candidate-confirming"]["state"] == "confirming"
    assert candidates["candidate-confirming"]["stage"] == "full_suite"


def test_winner_projection_without_evidence_is_explicit(tmp_path):
    db = RaceDB(tmp_path / "trace.db")
    db.append_event(
        {
            "event": "run_started",
            "run_id": "run-winner",
            "agent_id": "scheduler",
            "timestamp": "2026-01-01T00:00:00Z",
            "base_commit": "base-1",
        }
    )
    db.append_event(
        {
            "event": "candidate_submitted",
            "run_id": "run-winner",
            "agent_id": "agent-1",
            "candidate_id": "candidate-1",
            "timestamp": "2026-01-01T00:00:01Z",
            "patch_hash": "hash-1",
            "patch_path": "/tmp/candidate-1.patch",
        }
    )
    db.append_event(
        {
            "event": "candidate.won",
            "run_id": "run-winner",
            "agent_id": "verifier",
            "candidate_id": "candidate-1",
            "timestamp": "2026-01-01T00:00:02Z",
            "patch_hash": "hash-1",
        }
    )
    snapshot = db.run_snapshot("run-winner")
    assert snapshot["winner"]["candidate_id"] == "candidate-1"
    assert snapshot["winner"]["evidence_complete"] is False
    assert snapshot["winner"]["evidence"] == {}
    assert db.overview()["winners"] == {"total": 1, "full_suite_confirmed": 0}


def test_legacy_schema_is_migrated_and_reprojected(tmp_path):
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO schema_meta VALUES ('schema_version', '2');
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            cause TEXT,
            base_commit TEXT,
            config_fingerprint TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE run_events (
            run_id TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            event_id TEXT NOT NULL UNIQUE,
            event_type TEXT NOT NULL,
            agent_id TEXT,
            candidate_id TEXT,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (run_id, sequence)
        );
        CREATE TABLE agents (
            run_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            status TEXT NOT NULL,
            last_event_sequence INTEGER NOT NULL,
            PRIMARY KEY (run_id, agent_id)
        );
        CREATE TABLE candidates (
            run_id TEXT NOT NULL,
            candidate_id TEXT NOT NULL,
            agent_id TEXT,
            state TEXT NOT NULL,
            patch_hash TEXT,
            last_event_sequence INTEGER NOT NULL,
            PRIMARY KEY (run_id, candidate_id)
        );
        CREATE TABLE winners (
            run_id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL,
            patch_hash TEXT NOT NULL,
            base_commit TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            seq INTEGER,
            created_at REAL
        );
        """
    )
    events = [
        {
            "event": "run_configured",
            "run_id": "run-old",
            "agent_id": "scheduler",
            "timestamp": "2026-01-01T00:00:00Z",
            "base_commit": "old-base",
            "config_fingerprint": "old-config",
        },
        {
            "event": "run_started",
            "run_id": "run-old",
            "agent_id": "scheduler",
            "timestamp": "2026-01-01T00:00:01Z",
        },
        {
            "event": "agent_status",
            "run_id": "run-old",
            "agent_id": "agent-1",
            "timestamp": "2026-01-01T00:00:02Z",
            "status": "WORKING",
        },
        {
            "event": "candidate_submitted",
            "run_id": "run-old",
            "agent_id": "agent-1",
            "candidate_id": "candidate-1",
            "timestamp": "2026-01-01T00:00:03Z",
            "patch_hash": "old-hash",
            "patch_path": "/tmp/old.patch",
        },
        {
            "event": "arbiter.winner_declared",
            "run_id": "run-old",
            "agent_id": "arbiter",
            "candidate_id": "candidate-1",
            "timestamp": "2026-01-01T00:00:04Z",
            "patch_hash": "old-hash",
            "base_commit": "old-base",
        },
        {
            "event": "run_finished",
            "run_id": "run-old",
            "agent_id": "scheduler",
            "timestamp": "2026-01-01T00:00:05Z",
            "state": "COMPLETED",
        },
    ]
    for sequence, event in enumerate(events, start=1):
        payload = {**event, "event_id": f"legacy-{sequence}", "sequence": sequence}
        connection.execute(
            "INSERT INTO run_events VALUES(?,?,?,?,?,?,?,?)",
            (
                event["run_id"],
                sequence,
                payload["event_id"],
                event["event"],
                event.get("agent_id"),
                event.get("candidate_id"),
                json.dumps(payload, sort_keys=True),
                event["timestamp"],
            ),
        )
    connection.execute(
        "INSERT INTO runs(run_id, state, cause, base_commit, config_fingerprint, created_at, updated_at) "
        "VALUES('run-old', 'CANCELLING', 'winner_declared', 'old-base', 'old-config', "
        "'2026-01-01T00:00:00Z', '2026-01-01T00:00:05Z')"
    )
    connection.execute(
        "INSERT INTO agents VALUES('run-old', 'agent-1', 'WORKING', 3)"
    )
    connection.execute(
        "INSERT INTO candidates VALUES('run-old', 'candidate-1', 'agent-1', "
        "'CANDIDATE.WON', 'old-hash', 5)"
    )
    connection.execute(
        "INSERT INTO winners VALUES('run-old', 'candidate-1', 'old-hash', 'old-base', ?, NULL, 1.0)",
        (json.dumps({"complete_full_suite": True}),),
    )
    connection.commit()
    connection.close()

    db = RaceDB(path)
    assert db.overview()["schema_version"] == 3
    snapshot = db.run_snapshot("run-old")
    assert snapshot["run"]["state"] == "completed"
    assert snapshot["run"]["base_commit"] == "old-base"
    assert snapshot["run"]["config_fingerprint"] == "old-config"
    assert snapshot["agents"][0]["status"] == "working"
    assert snapshot["candidates"][0]["state"] == "won"
    assert snapshot["winner"]["candidate_id"] == "candidate-1"
    assert snapshot["winner"]["evidence_complete"] is True


def test_event_filters_and_latest_ordering(tmp_path):
    db = RaceDB(tmp_path / "trace.db")
    _append_lifecycle(db)
    latest = db.events("run-life", limit=2, latest=True)
    assert [event["sequence"] for event in latest] == [6, 7]
    older = db.events("run-life", before=4, limit=2, latest=True)
    assert [event["sequence"] for event in older] == [2, 3]
    typed = db.events("run-life", event_types=["agent_started", "agent_finished"])
    assert [event["event"] for event in typed] == ["agent_started", "agent_finished"]
    assert db.events("run-life", event_types=["missing"]) == []
