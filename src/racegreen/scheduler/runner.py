"""Race scheduler with bounded concurrency, verification, and cancellation."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..agent.events import EventStore
from ..agent.external import ExternalAgentProvider
from ..agent.models import AgentContext, AgentLimits, AgentStatus
from ..agent.provider import OpenAICompatibleProvider
from ..agent.runner import AgentRunner
from ..arbiter.arbiter import Arbiter
from ..arbiter.policy import FIRST_CONFIRMED, validate_policy
from ..storage.db import RaceDB
from ..verification.budget import VerificationBudget
from ..verification.config import VerificationConfig
from ..verification.intake import CandidateIntake
from ..verification.pipeline import VerificationJob, VerificationPipeline
from ..verification.runner import VerificationRunner
from ..workspace.manager import WorkspaceManager
from ..workspace.models import AgentWorkspace, RunLayout
from .budgets import BudgetExceeded, BudgetTracker, RunBudgets
from .cancellation import CancellationCoordinator, ProcessRecord
from .models import RunState, validate_run_transition
from .rate_limit import SharedRateLimiter

DEFAULT_DATABASE = Path.home() / ".racegreen" / "racegreen.db"


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
        database: Path | None = None,
        verification_config: VerificationConfig | None = None,
        winner_policy: str = FIRST_CONFIRMED,
        consensus_quorum: int | None = None,
        hedge_delay_seconds: float = 0.0,
        agent_models: list[str] | None = None,
        external_command: list[str] | None = None,
    ) -> None:
        self.repository = repository
        self.task = task
        self.agents_count = max(1, agents)
        self.max_concurrency = max_concurrency if max_concurrency is not None else max(1, self.agents_count)
        if self.max_concurrency < 1:
            raise ValueError("max-concurrency must be at least 1")
        if hedge_delay_seconds < 0:
            raise ValueError("hedge delay must not be negative")
        self.winner_policy = validate_policy(winner_policy)
        self.consensus_quorum = consensus_quorum
        self.hedge_delay_seconds = hedge_delay_seconds
        self.agent_models = [model for model in (agent_models or []) if model.strip()]
        self.external_command = list(external_command) if external_command else None
        if self.external_command is not None and not self.external_command:
            raise ValueError("external agent command must not be empty")
        self.manager = WorkspaceManager(repository, artifacts_root)
        self.budgets = budgets or RunBudgets()
        self.budget_tracker = BudgetTracker(self.budgets)
        self.cancellation = CancellationCoordinator()
        self.rate_limiter = SharedRateLimiter(
            max_per_second=max(1.0, self.budgets.max_candidates_total / max(1.0, self.budgets.max_wall_time_seconds)),
            burst=max(1, self.agents_count),
        )
        self.provider_factory = provider_factory
        self.allow_dirty = allow_dirty
        self.verification_config = verification_config or VerificationConfig()
        self._db = RaceDB(database or DEFAULT_DATABASE)
        self._run_id = uuid.uuid4().hex
        self._state = RunState.PREPARING
        self._lock = threading.RLock()
        self._candidate_counts: dict[str, int] = {}
        self._benchmark_timestamps: dict[str, float] = {}
        self._late_rejections = 0
        self._events: EventStore | None = None
        self.intake = CandidateIntake(enabled=self.verification_config.use_prefilter)
        self.arbiter: Arbiter | None = None
        self._pipeline: VerificationPipeline | None = None
        self._winner: dict[str, Any] | None = None
        self._winner_declared = False
        self._user_cancelled = False
        self._policy_decision: dict[str, Any] | None = None

    @property
    def run_id(self) -> str:
        return self._run_id

    def _set_state(self, nxt: RunState) -> None:
        with self._lock:
            validate_run_transition(self._state, nxt)
            self._state = nxt

    def _record_bench(self, key: str) -> None:
        self._benchmark_timestamps[key] = time.monotonic()

    def _spawn_in_pgroup(
        self,
        cmd: list[str],
        agent_id: str = "unknown",
        kind: str = "agent",
        **kwargs: Any,
    ) -> subprocess.Popen[Any]:
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
        self._user_cancelled = True
        self.cancellation.request_cancel()
        self._set_state(RunState.CANCELLING)
        if self._events is not None:
            try:
                self._events.mark_terminal()
            except Exception:
                pass
        if self._pipeline is not None:
            self._pipeline.cancel_verifications()
        self.intake.close()
        self.cancellation.terminate_all()
        self._record_bench("cancel_complete")

    def _on_winner(self, winner: dict[str, Any]) -> None:
        """Stop further work after a durable winner decision."""
        self._winner_declared = True
        with self._lock:
            self._winner = dict(winner)
        if self._pipeline is not None:
            self._pipeline.cancel_verifications()
        self.intake.close()
        self.cancellation.request_cancel()
        if self._events is not None:
            try:
                self._events.mark_terminal()
            except Exception:
                pass
        self.cancellation.terminate_all(grace_seconds=1.0)
        self._record_bench("winner_confirmed")

    def run(self) -> dict[str, Any]:
        layout = None
        events = None
        workspaces = []
        pipeline: VerificationPipeline | None = None
        try:
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
                    task=self.task,
                    repository=str(self.manager.repository.root),
                    agents=self.agents_count,
                    max_concurrency=self.max_concurrency,
                    winner_policy=self.winner_policy,
                    consensus_quorum=self.consensus_quorum,
                    full_suite_command=self.verification_config.full_suite_command,
                    test_command=self.verification_config.test_command,
                    max_full_suite_runs=self.verification_config.max_full_suite_runs_per_run,
                    max_changed_files=self.verification_config.max_changed_files,
                    max_changed_lines=self.verification_config.max_changed_lines,
                    max_patch_bytes=self.verification_config.max_patch_bytes,
                    sandbox=self.verification_config.sandbox,
                    sandbox_image=self.verification_config.sandbox_image,
                    sandbox_network=self.verification_config.sandbox_network,
                    sandbox_memory=self.verification_config.sandbox_memory,
                    sandbox_cpus=self.verification_config.sandbox_cpus,
                    agent_models=self.agent_models,
                    hedge_delay_seconds=self.hedge_delay_seconds,
                    external_command=self.external_command,
                    artifacts_root=str(self.manager.artifacts_root),
                    budgets={
                        "max_agent_wall_time_seconds": self.budgets.max_agent_wall_time_seconds,
                        "max_tokens_per_run": self.budgets.max_tokens_per_run,
                        "max_cost_usd_per_run": self.budgets.max_cost_usd_per_run,
                        "max_tool_calls_per_agent": self.budgets.max_tool_calls_per_agent,
                        "max_candidates_total": self.budgets.max_candidates_total,
                    },
                    config_fingerprint=hashlib.sha256(
                        json.dumps(
                            {
                                "verification": asdict(self.verification_config),
                                "winner_policy": self.winner_policy,
                                "consensus_quorum": self.consensus_quorum,
                                "agents": self.agents_count,
                                "max_concurrency": self.max_concurrency,
                                "agent_models": self.agent_models,
                                "external_command": self.external_command,
                                "hedge_delay_seconds": self.hedge_delay_seconds,
                                "budgets": {
                                    "max_agent_wall_time_seconds": self.budgets.max_agent_wall_time_seconds,
                                    "max_tokens_per_run": self.budgets.max_tokens_per_run,
                                    "max_cost_usd_per_run": self.budgets.max_cost_usd_per_run,
                                    "max_tool_calls_per_agent": self.budgets.max_tool_calls_per_agent,
                                    "max_candidates_total": self.budgets.max_candidates_total,
                                },
                            },
                            sort_keys=True,
                        ).encode()
                    ).hexdigest(),
                )
            except Exception as e:
                raise RuntimeError(f"event store failure: {e}") from e

            arbiter = Arbiter(self._db, events, run_dir=layout.root)
            self.arbiter = arbiter
            verifier = VerificationRunner(
                events,
                self.verification_config,
                budget=VerificationBudget(
                    self.verification_config.max_full_suite_runs_per_run
                ),
            )
            pipeline = VerificationPipeline(
                db=self._db,
                events=events,
                verifier=verifier,
                arbiter=arbiter,
                repository=self.manager.repository.root,
                intake=self.intake,
                max_workers=self.verification_config.max_parallel_verifications,
                on_winner=self._on_winner,
                winner_policy=self.winner_policy,
                consensus_quorum=self.consensus_quorum,
                total_agents=self.agents_count,
            )
            self._pipeline = pipeline
            pipeline.start()

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

            # Let queued verifications finish unless a winner already stopped
            # the race; in that case bound the wait so cancellation stays prompt.
            # Policies other than first_confirmed need every confirmation before
            # they can make a deterministic decision.
            close_timeout = (
                5.0
                if self._winner_declared and self.winner_policy == FIRST_CONFIRMED
                else None
            )
            pipeline.close(wait=True, timeout=close_timeout)
            if not self._user_cancelled and not arbiter.unverifiable:
                decision = pipeline.finalize()
                if decision is not None:
                    self._policy_decision = decision.as_dict()
            if self._winner_declared and self._policy_decision is None and self._winner:
                self._policy_decision = {
                    "policy": self._winner.get("winner_policy", self.winner_policy),
                    "selected": self._winner.get("candidate_id"),
                    "reason": self._winner.get("policy_reason", "earliest_confirmation"),
                    "considered": [],
                    "quorum": None,
                }
            self._pipeline = None

            if self._user_cancelled:
                try:
                    events.mark_terminal()
                except Exception:
                    pass
                self.cancellation.terminate_all()
                self._record_bench("cancel_complete")
                if self._state is not RunState.CANCELLING:
                    self._set_state(RunState.CANCELLING)
                self._set_state(RunState.CANCELLED)
            elif arbiter.unverifiable:
                self.cancellation.terminate_all()
                self._set_state(RunState.UNVERIFIABLE)
            elif self._winner_declared:
                try:
                    events.mark_terminal()
                except Exception:
                    pass
                self.cancellation.terminate_all(grace_seconds=1.0)
                self._record_bench("processes_exited")
                self._set_state(RunState.COMPLETED)
            else:
                # Agents may have submitted, but without full-suite confirmation
                # there is no winner. Do not report a successful race.
                self._set_state(RunState.EXHAUSTED)
            try:
                events.mark_terminal()
            except Exception:
                pass
            self._record_bench("processes_exited")
            winner = self._db.get_winner(self._run_id) if self._winner_declared else None
            summary: dict[str, Any] = {
                "run_id": self._run_id,
                "state": self._state.value,
                "results": results,
                "winner": winner,
                "winner_policy": self.winner_policy,
                "policy_decision": self._policy_decision,
                "verification": pipeline.results if pipeline is not None else [],
                "events_path": str(events.path),
                "artifacts_root": str(layout.root),
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
            if pipeline is not None:
                try:
                    pipeline.close(wait=False)
                except Exception:
                    pass

    def _run_agent(
        self, layout: RunLayout, workspace: AgentWorkspace, events: EventStore
    ) -> dict[str, Any]:
        agent_id = workspace.agent_id
        if (
            self.cancellation.is_cancelled
            or events.is_terminal()
            or self.intake.is_closed
            or self._winner_declared
        ):
            try:
                events.append_atomic("event.rejected_late", run_id=self._run_id, agent_id=agent_id, late_check=False)
            except Exception:
                pass
            return {"agent_id": agent_id, "status": AgentStatus.CANCELLED.value}
        index = self._agent_index(agent_id)
        if index > 0 and self.hedge_delay_seconds > 0:
            delay = self.hedge_delay_seconds * index
            if self.cancellation.wait(delay):
                return {"agent_id": agent_id, "status": AgentStatus.CANCELLED.value}
            self._record_bench(f"{agent_id}_hedged")
            try:
                events.append_atomic(
                    "agent.hedged",
                    run_id=self._run_id,
                    agent_id=agent_id,
                    late_check=False,
                    delay_seconds=delay,
                )
            except Exception:
                pass
        self._record_bench(f"{agent_id}_started")
        self._candidate_counts[agent_id] = 0
        try:
            provider = (
                ExternalAgentProvider(
                    self.external_command,
                    worktree=Path(workspace.path),
                    run_id=self._run_id,
                    agent_id=agent_id,
                    timeout_seconds=self.budgets.max_agent_wall_time_seconds,
                )
                if self.external_command is not None
                else self._make_provider(agent_id)
            )
            runner = AgentRunner(
                provider,
                events,
                intake=self.intake,
                is_terminal=events.is_terminal,
                usage_callback=self.budget_tracker.record_usage,
                rate_limiter=self.rate_limiter,
            )
            if self.cancellation.is_cancelled:
                runner.cancel()
            context = AgentContext(
                self._run_id,
                agent_id,
                str(workspace.path),
                self.task,
                AgentLimits(
                    timeout_seconds=self.budgets.max_agent_wall_time_seconds,
                    max_tool_calls=self.budgets.max_tool_calls_per_agent,
                    max_tool_output_bytes=self.budgets.max_output_bytes_per_agent,
                ),
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
                candidate = runner.last_candidate
                if candidate is None:
                    return {"agent_id": agent_id, "status": status.value, "verification": "missing_snapshot"}
                if self._pipeline is None or self.intake.is_closed or self._winner_declared:
                    return {"agent_id": agent_id, "status": status.value, "verification": "not_submitted"}
                changed = self._changed_paths(Path(workspace.path), layout.base.head)
                job = VerificationJob(
                    run_id=self._run_id,
                    agent_id=agent_id,
                    candidate_id=candidate.candidate_id,
                    patch_path=candidate.patch_path,
                    patch_hash=candidate.patch_hash,
                    base_commit=candidate.base_commit,
                    changed_paths=changed,
                )
                queued = self._pipeline.submit(job)
                self._record_bench("candidate_received")
                return {
                    "agent_id": agent_id,
                    "status": status.value,
                    "candidate_id": candidate.candidate_id,
                    "verification": "queued" if queued else "queue_full",
                }
            return {"agent_id": agent_id, "status": status.value}
        except BudgetExceeded:
            raise
        except Exception as e:
            return {"agent_id": agent_id, "status": "failed", "error": str(e)}

    @staticmethod
    def _agent_index(agent_id: str) -> int:
        try:
            return max(0, int(agent_id.rsplit("-", 1)[-1]) - 1)
        except (TypeError, ValueError):
            return 0

    def _make_provider(self, agent_id: str) -> Any:
        if not self.provider_factory:
            from ..agent.config import ProviderConfig

            config = ProviderConfig.from_environment(repository=self.manager.repository.root)
            index = self._agent_index(agent_id)
            model = (
                self.agent_models[index % len(self.agent_models)]
                if self.agent_models
                else config.model
            )
            return OpenAICompatibleProvider(
                endpoint=config.endpoint,
                model=model,
                api_key_env=config.api_key_env,
                timeout_seconds=config.timeout_seconds,
                requires_api_key=config.requires_api_key,
            )
        try:
            return self.provider_factory(agent_id)
        except TypeError:
            return self.provider_factory()

    @staticmethod
    def _changed_paths(worktree: Path, base_commit: str) -> tuple[str, ...]:
        try:
            result = subprocess.run(
                ["git", "-C", str(worktree), "diff", "--name-only", base_commit, "--"],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return ()
        if result.returncode != 0:
            return ()
        return tuple(line for line in result.stdout.splitlines() if line)
