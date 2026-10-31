
from racegreen.agent.events import EventStore
from racegreen.arbiter.arbiter import Arbiter
from racegreen.storage.db import RaceDB
from racegreen.verification.evidence import VerificationEvidence


def test_atomic_cas(tmp_path):
    db = RaceDB(tmp_path / "race.db")
    ev = EventStore(tmp_path / "events.jsonl")
    arb = Arbiter(db, ev)
    e = VerificationEvidence("c1", "h1", "b1", ("pytest",), "", "jh", 0, ())
    res = arb.declare_winner("r1", "c1", "h1", "b1", e)
    assert res["status"] == "won"
    res2 = arb.declare_winner("r1", "c2", "h2", "b1", e)
    assert res2["status"] == "confirmed_but_not_selected"
    w = db.get_winner("r1")
    assert w["candidate_id"] == "c1"
