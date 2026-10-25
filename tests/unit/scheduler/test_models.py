from racegreen.scheduler.models import (
    CandidateState,
    RunState,
    assert_candidate_phase3_safe,
    validate_candidate_transition,
    validate_run_transition,
    InvalidStateTransition,
)
import pytest


def test_run_states_include_cancelling_and_completed():
    assert RunState.CANCELLING.value == "cancelling"
    assert RunState.COMPLETED.value == "completed"


def test_run_transitions():
    validate_run_transition(RunState.PREPARING, RunState.RUNNING)
    validate_run_transition(RunState.RUNNING, RunState.CANCELLING)
    validate_run_transition(RunState.CANCELLING, RunState.CANCELLED)
    validate_run_transition(RunState.RUNNING, RunState.COMPLETED)
    with pytest.raises(InvalidStateTransition):
        validate_run_transition(RunState.COMPLETED, RunState.RUNNING)


def test_candidate_won_only_from_confirming():
    validate_candidate_transition(CandidateState.CONFIRMING, CandidateState.WON)
    with pytest.raises(InvalidStateTransition):
        validate_candidate_transition(CandidateState.PREFILTERING, CandidateState.WON)
    with pytest.raises(InvalidStateTransition):
        assert_candidate_phase3_safe(CandidateState.WON, CandidateState.RECEIVED)
    # assert_candidate_phase3_safe allows from CONFIRMING
    assert_candidate_phase3_safe(CandidateState.WON, CandidateState.CONFIRMING)
