from racegreen.verification.evidence import VerificationEvidence
from racegreen.verification.winner import WinnerSlot


def test_winner_atomic():
    w = WinnerSlot()
    e = VerificationEvidence("c1", "h1", "b1", ("pytest",), "", "j1", 0, ())
    assert w.try_set_winner({"candidate_id": "c1", "evidence": e.__dict__}) is True
    assert w.try_set_winner({"candidate_id": "c2"}) is False
    assert w.get_winner()["candidate_id"] == "c1"


def test_evidence_typed():
    e = VerificationEvidence(
        "c1", "h1", "b1", ("pytest", "-q"), "fp", "jh", 1, (), complete_full_suite=True
    )
    assert e.complete_full_suite is True
    assert e.argv == ("pytest", "-q")
    assert (
        VerificationEvidence("c1", "h1", "b1", ("pytest",), "", "jh", 0, ())
        .complete_full_suite
        is False
    )
