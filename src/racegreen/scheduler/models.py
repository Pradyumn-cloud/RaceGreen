"""Run and candidate finite state machines."""

from __future__ import annotations

from enum import StrEnum


class RunState(StrEnum):
    PREPARING = "preparing"
    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    EXHAUSTED = "exhausted"
    FAILED = "failed"


class CandidateState(StrEnum):
    RECEIVED = "received"
    PREFILTERING = "prefiltering"
    SELECTED_TESTS = "selected_tests"
    CONFIRMING = "confirming"
    WON = "won"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    INFRA_FAILED = "infra_failed"


_RUN_TRANSITIONS: dict[RunState, set[RunState]] = {
    RunState.PREPARING: {RunState.RUNNING, RunState.CANCELLED, RunState.FAILED},
    RunState.RUNNING: {
        RunState.CANCELLING,
        RunState.COMPLETED,
        RunState.EXHAUSTED,
        RunState.CANCELLED,
        RunState.FAILED,
    },
    RunState.CANCELLING: {RunState.CANCELLED, RunState.COMPLETED, RunState.FAILED, RunState.EXHAUSTED},
    RunState.CANCELLED: set(),
    RunState.COMPLETED: set(),
    RunState.EXHAUSTED: set(),
    RunState.FAILED: set(),
}


_CANDIDATE_TRANSITIONS_PHASE3: dict[CandidateState, set[CandidateState]] = {
    CandidateState.RECEIVED: {CandidateState.PREFILTERING, CandidateState.CANCELLED, CandidateState.INFRA_FAILED, CandidateState.REJECTED},
    CandidateState.PREFILTERING: {CandidateState.SELECTED_TESTS, CandidateState.CANCELLED, CandidateState.INFRA_FAILED, CandidateState.REJECTED},
    CandidateState.SELECTED_TESTS: {CandidateState.CONFIRMING, CandidateState.CANCELLED, CandidateState.INFRA_FAILED, CandidateState.REJECTED},
    CandidateState.CONFIRMING: {CandidateState.WON, CandidateState.REJECTED, CandidateState.CANCELLED, CandidateState.INFRA_FAILED},
    CandidateState.WON: set(),
    CandidateState.REJECTED: set(),
    CandidateState.CANCELLED: set(),
    CandidateState.INFRA_FAILED: set(),
}


class InvalidStateTransition(RuntimeError):
    pass


def validate_run_transition(current: RunState, nxt: RunState) -> None:
    if nxt not in _RUN_TRANSITIONS.get(current, set()):
        raise InvalidStateTransition(f"invalid run transition {current} -> {nxt}")


def validate_candidate_transition(current: CandidateState, nxt: CandidateState) -> None:
    if nxt not in _CANDIDATE_TRANSITIONS_PHASE3.get(current, set()):
        raise InvalidStateTransition(f"invalid candidate transition {current} -> {nxt}")


def assert_candidate_phase3_safe(nxt: CandidateState, current: CandidateState) -> None:
    """Guard: in Phase 3 candidates only get intake/snapshot/hash. WON only from CONFIRMING with full-suite evidence (Phase 5)."""
    if nxt == CandidateState.WON and current != CandidateState.CONFIRMING:
        raise InvalidStateTransition("candidate cannot reach WON from non-CONFIRMING state in Phase 3")
    # Also ensure we don't create paths to WON except via CONFIRMING
    if nxt == CandidateState.WON:
        return  # only allowed if coming from CONFIRMING - validated by transition table context
    validate_candidate_transition(current, nxt)
