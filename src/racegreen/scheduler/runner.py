"""Race scheduler with bounded concurrency, cancellation, and budgets."""

from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from ..agent.events import EventStore
from ..agent.models import AgentContext, AgentStatus
from ..agent.provider import OpenAICompatibleProvider
from ..agent.runner import AgentRunner
from ..verification.intake import CandidateIntake
from ..workspace.manager import WorkspaceManager
from .budgets import BudgetExceeded, BudgetTracker, RunBudgets
from .cancellation import CancellationCoordinator, ProcessRecord
from .models import RunState, validate_run_transition
from .rate_limit import SharedRateLimiter


class RaceScheduler:
    def __init__(
        self,
        repository: Path,
        task: str,
        *,
        agents: int = 2,
        max_concurrency: int | None = None,
        artifacts_root: Path | None = None,
        budgets: RunBudgets | None = None,
        provider_factory: Any = None,
        allow_dirty: bool = False,
    ) -> None:
        self.repository = repository
        self.task = task
        self.agents_count = max(1, agents)
        self.max_concurrency = max_concurrency if max_concurrency is not None else max(1, self.agents_count)
        if self.max_concurrency < 1:
            raise ValueError("max-concurrency must be at least 1")
        self.manager = WorkspaceManager(repository, artifacts_root)
        self.budgets = budgets or RunBudgets()
        self.budget_tracker = BudgetTracker(self.budgets)
        self.cancellation = CancellationCoordinator()
        self.rate_limiter = SharedRateLimiter()
        self.provider_factory = provider_factory
        self.allow_dirty = allow_dirty
        self._run_id = uuid.uuid4().hex
        self._state = RunState.PREPARING
        self._lock = threading.RLock()
        self._candidate_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=100)
        self._candidate_counts: dict[str, int] = {}
        self._benchmark_timestamps: dict[str, float] = {}
        self._late_rejections = 0
        self._events: EventStore | None = None
        self.intake = CandidateIntake()

    @property
    def run_id(self) -> str:
        return self._run_id

    def _set_state(self, nxt: RunState) -> None:
        with self._lock:
            validate_run_transition(self._state, nxt)
            self._state = nxt

    def _record_bench(self, key: str) -> None:
        self._benchmark_timestamps[key] = time.monotonic()

    def _spawn_in_pgroup(self, cmd: list[str], agent_id: str = "unknown", kind: str = "agent", **kwargs) -> subprocess.Popen:
        preexec_fn = os.setsid if hasattr(os, "setsid") else None
        proc = subprocess.Popen(cmd, preexec_fn=preexec_fn, **kwargs)
        try:
            if hasattr(os, "getpgid"):
                pgid = os.getpgid(proc.pid)
            else:
                pgid = proc.pid
        except Exception:
            pgid = proc.pid
        self.cancellation.register_process(
            ProcessRecord(pid=proc.pid, pgid=pgid, run_id=self._run_id, agent_id=agent_id, kind=kind)
        )
        return proc

    def cancel(self) -> None:
        self.cancellation.request_cancel()
        self._set_state(RunState.CANCELLING)
        if self._events is not None:
            try:
                self._events.mark_terminal()
            except Exception:
                pass
        self.cancellation.terminate_all()
        self._record_bench("cancel_complete")

    def run(self) -> dict[str, Any]:
        layout = None
        events = None
        workspaces = []
        try:
            os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
            self._record_bench("run_start")
            layout = self.manager.create_run(allow_dirty=self.allow_dirty)
            self._run_id = layout.run_id
            self._record_bench("workspace_ready")
            events = EventStore(layout.artifacts / "run_events.jsonl")
            self._events = events
            self.intake.events = events
            self._set_state(RunState.RUNNING)
            try:
                events.append_atomic(
                    "run_started",
                    run_id=self._run_id,
                    agent_id="scheduler",
                    late_check=False,
                    base_commit=layout.base.head,
                    agents=self.agents_count,
                )
            except Exception as e:
                raise RuntimeError(f"event store failure: {e}") from e
            for i in range(self.agents_count):
                agent_id = f"agent-{i + 1}"
                try:
                    ws = self.manager.create_agent_workspace(layout, agent_id)
                    workspaces.append(ws)
                    self.budget_tracker.start_agent(agent_id)
                except Exception:
                    for ws in workspaces:
                        try:
                            self.manager.cleanup_agent_workspace(layout, ws)
                        except Exception:
                            pass
                    raise
            results = []
            with ThreadPoolExecutor(max_workers=self.max_concurrency) as executor:
                futs = []
                for ws in workspaces:
                    futs.append(executor.submit(self._run_agent, layout, ws, events))
                for fut in as_completed(futs):
                    try:
                        res = fut.result()
                        results.append(res)
                    except BudgetExceeded as e:
                        results.append({"error": str(e), "status": "budget_exceeded"})
                    except Exception as e:
                        results.append({"error": str(e), "status": "failed"})
            if self.cancellation.is_cancelled:
                try:
                    events.mark_terminal()
                except Exception:
                    pass
                self._set_state(RunState.CANCELLING)
                self.cancellation.terminate_all()
                self._record_bench("cancel_complete")
                self._set_state(RunState.CANCELLED)
                terminal = RunState.CANCELLED
            else:
                terminal = RunState.COMPLETED if any(r.get("status") == AgentStatus.SUBMITTED.value for r in results) else RunState.EXHAUSTED
                self._set_state(terminal)
            try:
                events.mark_terminal()
            except Exception:
                pass
            self._record_bench("processes_exited")
            summary = {
                "run_id": self._run_id,
                "state": self._state.value,
                "results": results,
                "benchmark_timestamps": self._benchmark_timestamps,
                "cancel_latency": self.cancellation.cancel_latency(),
            }
            try:
                events.append_atomic("run_finished", agent_id="scheduler", late_check=False, **summary)
            except Exception as e:
                raise RuntimeError(f"event store failure: {e}") from e
            return summary
        except Exception as e:
            if events is not None:
                try:
                    events.mark_terminal()
                    events.append_atomic("run_failed", run_id=self._run_id, agent_id="scheduler", late_check=False, error=str(e))
                except Exception:
                    pass
            self._state = RunState.FAILED
            raise
        finally:
            pass

    def _run_agent(self, layout, workspace, events: EventStore) -> dict[str, Any]:
        agent_id = workspace.agent_id
        if self.cancellation.is_cancelled or events.is_terminal():
            try:
                events.append_atomic("event.rejected_late", run_id=self._run_id, agent_id=agent_id, late_check=False)
            except Exception:
                pass
            return {"agent_id": agent_id, "status": AgentStatus.CANCELLED.value}
        self._record_bench(f"{agent_id}_started")
        self._candidate_counts[agent_id] = 0
        try:
            if self.provider_factory:
                provider = self.provider_factory()
            else:
                from ..agent.config import ProviderConfig

                config = ProviderConfig.from_environment()
                provider = OpenAICompatibleProvider(
                    endpoint=config.endpoint,
                    model=config.model,
                    api_key_env=config.api_key_env,
                    timeout_seconds=config.timeout_seconds,
                    requires_api_key=config.requires_api_key,
                )
            runner = AgentRunner(provider, events, intake=self.intake, is_terminal=events.is_terminal())
            if self.cancellation.is_cancelled:
                runner.cancel()
            context = AgentContext(
                self._run_id,
                agent_id,
                str(workspace.path),
                self.task,
            )
            self._record_bench("first_llm_call")
            status = runner.run(
                context,
                base_commit=layout.base.head,
                candidate_dir=layout.artifacts / "candidates",
            )
            if status == AgentStatus.SUBMITTED:
                try:
                    self.budget_tracker.record_candidate(agent_id)
                except Exception:
                    pass
                try:
                    self._candidate_queue.put({"agent_id": agent_id, "run_id": self._run_id}, timeout=1.0)
                    self._record_bench("candidate_received")
                except Exception:
                    pass
            return {"agent_id": agent_id, "status": status.value}
        except Exception as e:
            return {"agent_id": agent_id, "status": "failed", "error": str(e)}
