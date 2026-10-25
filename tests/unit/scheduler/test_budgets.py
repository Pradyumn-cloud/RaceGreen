import time

import pytest

from racegreen.scheduler.budgets import BudgetExceeded, BudgetTracker, RunBudgets


def test_budget_tracking_atomic_and_monotonic():
    b = BudgetTracker(RunBudgets(max_tokens_per_agent=10, max_tool_calls_per_agent=2))
    b.start_agent("a1")
    b.record_usage("a1", tokens=5, tool_calls=1)
    b.record_usage("a1", tokens=3, tool_calls=1)
    with pytest.raises(BudgetExceeded):
        b.record_usage("a1", tokens=3, tool_calls=1)  # exceeds tokens
    b2 = BudgetTracker(RunBudgets(max_candidates_per_agent=1))
    b2.start_agent("a2")
    b2.record_candidate("a2")
    with pytest.raises(BudgetExceeded):
        b2.record_candidate("a2")
