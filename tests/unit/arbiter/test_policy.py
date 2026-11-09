from racegreen.arbiter.policy import (
    BEST_OF_CONFIRMED,
    CONSENSUS,
    FIRST_CONFIRMED,
    ConfirmedCandidate,
    consensus_quorum,
    select_winner,
)


def _candidate(
    candidate_id: str,
    *,
    patch_hash: str = "hash",
    confirmed_at: float = 0.0,
    changed_lines: int = 10,
    changed_files: int = 1,
    agent_id: str | None = None,
) -> ConfirmedCandidate:
    return ConfirmedCandidate(
        candidate_id=candidate_id,
        agent_id=agent_id or f"agent-{candidate_id}",
        patch_hash=patch_hash,
        base_commit="base",
        evidence={"complete_full_suite": True},
        confirmed_at=confirmed_at,
        changed_files=changed_files,
        changed_lines=changed_lines,
    )


def test_first_confirmed_is_deterministic() -> None:
    candidates = [
        _candidate("late", confirmed_at=2.0),
        _candidate("early", confirmed_at=1.0),
    ]
    decision = select_winner(FIRST_CONFIRMED, candidates, total_agents=2)
    assert decision.selected is not None
    assert decision.selected.candidate_id == "early"
    assert decision.reason == "earliest_confirmation"


def test_best_of_confirmed_prefers_smallest_patch_then_earliest() -> None:
    candidates = [
        _candidate("large", confirmed_at=1.0, changed_lines=20),
        _candidate("small-late", confirmed_at=3.0, changed_lines=5),
        _candidate("small-early", confirmed_at=2.0, changed_lines=5),
    ]
    decision = select_winner(BEST_OF_CONFIRMED, candidates, total_agents=3)
    assert decision.selected is not None
    assert decision.selected.candidate_id == "small-early"
    assert decision.reason == "smallest_confirmed_patch"


def test_consensus_requires_matching_patch_hashes() -> None:
    candidates = [
        _candidate("a", patch_hash="alpha", confirmed_at=1.0),
        _candidate("b", patch_hash="beta", confirmed_at=2.0),
        _candidate("c", patch_hash="alpha", confirmed_at=3.0),
    ]
    decision = select_winner(CONSENSUS, candidates, total_agents=3)
    assert decision.selected is not None
    assert decision.selected.candidate_id == "a"
    assert decision.quorum == 2


def test_consensus_counts_distinct_agents_not_duplicate_submissions() -> None:
    candidates = [
        _candidate("a1", patch_hash="alpha", confirmed_at=1.0, agent_id="agent-1"),
        _candidate("a2", patch_hash="alpha", confirmed_at=2.0, agent_id="agent-1"),
    ]
    decision = select_winner(CONSENSUS, candidates, total_agents=2)
    assert decision.selected is None
    assert decision.reason == "no_consensus"


def test_consensus_without_quorum_does_not_declare_a_winner() -> None:
    candidates = [
        _candidate("a", patch_hash="alpha", confirmed_at=1.0),
        _candidate("b", patch_hash="beta", confirmed_at=2.0),
    ]
    decision = select_winner(CONSENSUS, candidates, total_agents=3)
    assert decision.selected is None
    assert decision.reason == "no_consensus"
    assert decision.quorum == 2


def test_consensus_quorum_is_bounded_by_agent_count() -> None:
    assert consensus_quorum(1) == 1
    assert consensus_quorum(2) == 2
    assert consensus_quorum(3) == 2
    assert consensus_quorum(4) == 3
    assert consensus_quorum(5) == 3
    assert consensus_quorum(2, configured=10) == 2


def test_no_confirmed_candidates_is_explicit() -> None:
    decision = select_winner(CONSENSUS, [], total_agents=2)
    assert decision.selected is None
    assert decision.reason == "no_confirmed_candidates"
