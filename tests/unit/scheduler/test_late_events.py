from pathlib import Path

from racegreen.agent.events import EventStore


def test_atomic_late_rejection_records_event_rejected_late(tmp_path):
    events = EventStore(tmp_path / "events.jsonl")
    events.mark_terminal()
    res = events.append_atomic("candidate_submitted", run_id="r1", agent_id="a1", late_check=True, candidate_id="c1")
    assert res is None
    recs = events.replay()
    assert any(r.get("event") == "event.rejected_late" for r in recs)
