
from racegreen.agent.events import EventStore
from racegreen.arbiter.arbiter import Arbiter
from racegreen.storage.db import RaceDB
from racegreen.verification.evidence import VerificationEvidence


def test_atomic_cas(tmp_path):
    db = RaceDB(tmp_path / "race.db")
    ev = EventStore(tmp_path / "events.jsonl")
    arb = Arbiter(db, ev)
    e = VerificationEvidence(
        "c1", "h1", "b1", ("pytest",), "", "jh", 0, (), complete_full_suite=True
    )
    res = arb.declare_winner("r1", "c1", "h1", "b1", e)
    assert res["status"] == "won"
    e2 = VerificationEvidence(
        "c2", "h2", "b1", ("pytest",), "", "jh2", 0, (), complete_full_suite=True
    )
    res2 = arb.declare_winner("r1", "c2", "h2", "b1", e2)
    assert res2["status"] == "confirmed_but_not_selected"
    w = db.get_winner("r1")
    assert w["candidate_id"] == "c1"


def test_arbiter_rejects_incomplete_evidence(tmp_path):
    db = RaceDB(tmp_path / "race.db")
    ev = EventStore(tmp_path / "events.jsonl")
    arb = Arbiter(db, ev)
    incomplete = VerificationEvidence(
        "c1", "h1", "b1", ("pytest",), "", "jh", 0, (), complete_full_suite=False
    )
    res = arb.declare_winner("r1", "c1", "h1", "b1", incomplete)
    assert res["status"] == "rejected"
    assert db.get_winner("r1") is None
