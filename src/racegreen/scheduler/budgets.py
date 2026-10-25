"""Budget tracking with monotonic clock and atomic accounting."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RunBudgets:
    max_wall_time_seconds: float = 600.0
    max_agent_wall_time_seconds: float = 300.0
    max_tokens_per_run: int | None = None
    max_tokens_per_agent: int | None = None
    max_cost_usd_per_run: float | None = None  # free tier primary cost is tokens; keep for compatibility
    max_candidates_total: int = 100
    max_candidates_per_agent: int = 10
    max_candidate_artifact_bytes: int = 10 * 1024 * 1024
    max_tool_calls_per_agent: int = 100
    max_output_bytes_per_agent: int = 32_000
    max_iterations_per_agent: int = 100


@dataclass
class BudgetUsage:
    wall_time_seconds: float = 0.0
    tokens: int = 0
    cost_usd: float = 0.0
    tool_calls: int = 0
    output_bytes: int = 0
    candidates: int = 0
    in_flight_overshoot: float = 0.0


class BudgetExceeded(RuntimeError):
    pass


class BudgetTracker:
    def __init__(self, budgets: RunBudgets) -> None:
        self.budgets = budgets
        self._lock = threading.RLock()
        self._run_usage = BudgetUsage()
        self._agent_usage: dict[str, BudgetUsage] = {}
        self._agent_start: dict[str, float] = {}
        self._run_start = time.monotonic()

    def start_agent(self, agent_id: str) -> None:
        with self._lock:
            if agent_id not in self._agent_usage:
                self._agent_usage[agent_id] = BudgetUsage()
            self._agent_start[agent_id] = time.monotonic()

    def _check_time(self, agent_id: str | None = None) -> None:
        now = time.monotonic()
        run_elapsed = now - self._run_start
        if run_elapsed > self.budgets.max_wall_time_seconds:
            raise BudgetExceeded("run wall time budget exceeded")
        if agent_id and agent_id in self._agent_start:
            agent_elapsed = now - self._agent_start[agent_id]
            if agent_elapsed > self.budgets.max_agent_wall_time_seconds:
                raise BudgetExceeded(f"agent {agent_id} wall time budget exceeded")

    def record_usage(self, agent_id: str, *, tokens: int = 0, cost_usd: float = 0.0, tool_calls: int = 0, output_bytes: int = 0) -> None:
        with self._lock:
            self._check_time(agent_id)
            if agent_id not in self._agent_usage:
                self.start_agent(agent_id)
            usage = self._agent_usage[agent_id]
            usage.tokens += tokens
            usage.cost_usd += cost_usd
            usage.tool_calls += tool_calls
            usage.output_bytes += output_bytes
            self._run_usage.tokens += tokens
            self._run_usage.cost_usd += cost_usd
            self._run_usage.tool_calls += tool_calls
            self._run_usage.output_bytes += output_bytes
            if self.budgets.max_tokens_per_agent is not None and usage.tokens > self.budgets.max_tokens_per_agent:
                raise BudgetExceeded(f"agent {agent_id} token budget exceeded")
            if self.budgets.max_tokens_per_run is not None and self._run_usage.tokens > self.budgets.max_tokens_per_run:
                raise BudgetExceeded("run token budget exceeded")
            if self.budgets.max_cost_usd_per_run is not None and self._run_usage.cost_usd > self.budgets.max_cost_usd_per_run:
                raise BudgetExceeded("run cost budget exceeded")
            if usage.tool_calls > self.budgets.max_tool_calls_per_agent:
                raise BudgetExceeded(f"agent {agent_id} tool call budget exceeded")
            if usage.output_bytes > self.budgets.max_output_bytes_per_agent:
                raise BudgetExceeded(f"agent {agent_id} output bytes budget exceeded")

    def record_candidate(self, agent_id: str) -> None:
        with self._lock:
            self._check_time(agent_id)
            if agent_id not in self._agent_usage:
                self.start_agent(agent_id)
            usage = self._agent_usage[agent_id]
            usage.candidates += 1
            self._run_usage.candidates += 1
            if usage.candidates > self.budgets.max_candidates_per_agent:
                raise BudgetExceeded(f"agent {agent_id} candidate budget exceeded")
            if self._run_usage.candidates > self.budgets.max_candidates_total:
                raise BudgetExceeded("run candidate budget exceeded")

    def check_candidate_size(self, size_bytes: int) -> None:
        if size_bytes > self.budgets.max_candidate_artifact_bytes:
            raise BudgetExceeded("candidate artifact exceeds size limit")

    def get_usage(self, agent_id: str | None = None) -> BudgetUsage:
        with self._lock:
            if agent_id is not None:
                return self._agent_usage.get(agent_id, BudgetUsage())
            return self._run_usage
