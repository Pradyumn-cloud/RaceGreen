"""Bounded candidate verification worker pool.

The scheduler submits immutable candidate snapshots here. Workers run the
staged verifier. Depending on the configured winner policy, a confirmed
candidate is either submitted to the arbiter immediately or held until all
verifications finish and a deterministic policy chooses among them.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..agent.events import EventStore
from ..arbiter.arbiter import Arbiter
from ..arbiter.policy import (
    FIRST_CONFIRMED,
    ConfirmedCandidate,
    PolicyDecision,
    select_winner,
    validate_policy,
)
from ..storage.db import RaceDB
from ..verification.intake import CandidateIntake
from ..verification.runner import VerificationRunner

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VerificationJob:
    run_id: str
    agent_id: str
    candidate_id: str
    patch_path: Path
    patch_hash: str
    base_commit: str
    changed_paths: tuple[str, ...] = ()


class VerificationPipeline:
    """Consume candidate jobs with bounded concurrency and a winner policy."""

    def __init__(
        self,
        *,
        db: RaceDB,
        events: EventStore,
        verifier: VerificationRunner,
        arbiter: Arbiter,
        repository: Path,
        intake: CandidateIntake | None = None,
        max_workers: int = 2,
        on_winner: Callable[[dict[str, Any]], None] | None = None,
        queue_size: int = 100,
        winner_policy: str = FIRST_CONFIRMED,
        consensus_quorum: int | None = None,
        total_agents: int = 1,
    ) -> None:
        self.db = db
        self.events = events
        self.verifier = verifier
        self.arbiter = arbiter
        self.repository = repository
        self.intake = intake
        self.max_workers = max(1, max_workers)
        self.on_winner = on_winner
        self.winner_policy = validate_policy(winner_policy)
        self.consensus_quorum = consensus_quorum
        self.total_agents = max(1, total_agents)
        self._queue: queue.Queue[VerificationJob | None] = queue.Queue(
            maxsize=max(1, queue_size)
        )
        self._workers: list[threading.Thread] = []
        self._lock = threading.RLock()
        self._results: list[dict[str, Any]] = []
        self._confirmed: list[tuple[VerificationJob, ConfirmedCandidate, dict[str, Any]]] = []
        self._winner: dict[str, Any] | None = None
        self._winner_event = threading.Event()
        self._policy_decision: PolicyDecision | None = None
        self._finalized = False
        self._closed = False

    @property
    def winner(self) -> dict[str, Any] | None:
        with self._lock:
            return dict(self._winner) if self._winner else None

    @property
    def results(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._results]

    @property
    def policy_decision(self) -> PolicyDecision | None:
        with self._lock:
            return self._policy_decision

    def has_winner(self) -> bool:
        return self._winner_event.is_set()

    def cancel_verifications(self) -> None:
        """Stop in-flight verification commands after a winner or user cancel."""
        self.verifier.cancel()

    def start(self) -> None:
        with self._lock:
            if self._workers:
                return
            for index in range(self.max_workers):
                worker = threading.Thread(
                    target=self._worker,
                    name=f"racegreen-verify-{index + 1}",
                    daemon=True,
                )
                worker.start()
                self._workers.append(worker)

    def submit(self, job: VerificationJob) -> bool:
        with self._lock:
            if self._closed or self._winner_event.is_set():
                return False
        try:
            self._queue.put(job, timeout=1.0)
            return True
        except queue.Full:
            return False

    def close(self, wait: bool = True, timeout: float | None = None) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            workers = list(self._workers)
        for _ in workers:
            while True:
                try:
                    self._queue.put(None, timeout=1.0)
                    break
                except queue.Full:
                    # A full queue means workers are still draining accepted
                    # jobs. Do not drop a sentinel: every live worker needs one
                    # to exit, otherwise join() can time out and leak threads.
                    if not any(worker.is_alive() for worker in workers):
                        break
        if wait:
            for worker in workers:
                worker.join(timeout=timeout)

    def finalize(self) -> PolicyDecision | None:
        """Apply the configured policy after all verification work is done.

        ``first_confirmed`` already declared a winner from a worker, so this
        returns that policy's decision when available. Other policies hold
        every confirmed candidate until this call, then choose deterministically.
        """
        with self._lock:
            if self._finalized:
                return self._policy_decision
            self._finalized = True
            confirmed = list(self._confirmed)
            if self.winner_policy == FIRST_CONFIRMED:
                self._policy_decision = (
                    None if self._winner is None else self._policy_decision
                )
                return self._policy_decision

        decision = select_winner(
            self.winner_policy,
            [item[1] for item in confirmed],
            total_agents=self.total_agents,
            configured_quorum=self.consensus_quorum,
        )
        with self._lock:
            self._policy_decision = decision
        self._emit_policy_decision(decision)

        by_id = {item[1].candidate_id: item for item in confirmed}
        selected = by_id.get(decision.selected.candidate_id) if decision.selected else None
        for job, candidate, result in confirmed:
            if selected is not None and candidate.candidate_id == selected[1].candidate_id:
                continue
            self._record(
                job,
                {
                    **result,
                    "status": "confirmed_but_not_selected",
                    "policy": decision.policy,
                    "reason": decision.reason,
                },
            )
            self._emit(
                job,
                "candidate.confirmed_but_not_selected",
                patch_hash=job.patch_hash,
                policy=decision.policy,
                reason=decision.reason,
            )
        if selected is not None:
            self._declare(selected[0], selected[1], selected[2], decision)
        return decision

    def _worker(self) -> None:
        while True:
            job = self._queue.get()
            try:
                if job is None:
                    return
                if self._winner_event.is_set():
                    self._record(
                        job,
                        {
                            "status": "cancelled",
                            "stage": "queue",
                            "reason": "winner_already_confirmed",
                        },
                    )
                    self._emit(
                        job,
                        "candidate.cancelled",
                        reason="winner_already_confirmed",
                        patch_hash=job.patch_hash,
                    )
                    continue
                self._verify(job)
            except Exception as exc:  # noqa: BLE001 - worker boundary
                if job is not None:
                    self._record(
                        job,
                        {
                            "status": "rejected",
                            "stage": "worker",
                            "classification": "infra",
                            "reason": f"{type(exc).__name__}: {exc}",
                        },
                    )
                    self._emit(
                        job,
                        "candidate.rejected",
                        stage="worker",
                        classification="infra",
                        reason=f"{type(exc).__name__}: {exc}",
                        patch_hash=job.patch_hash,
                    )
            finally:
                self._queue.task_done()

    def _verify(self, job: VerificationJob) -> None:
        result = self.verifier.verify_candidate(
            self.repository,
            self.repository,
            job.candidate_id,
            job.patch_path,
            job.patch_hash,
            job.base_commit,
            job.run_id,
            job.agent_id,
            job.changed_paths,
        )
        if result.get("status") != "passed":
            self._record(job, result)
            return

        evidence = result.get("evidence")
        if not isinstance(evidence, dict):
            evidence = {}
        confirmed = ConfirmedCandidate(
            candidate_id=job.candidate_id,
            agent_id=job.agent_id,
            patch_hash=job.patch_hash,
            base_commit=job.base_commit,
            evidence=evidence,
            confirmed_at=time.monotonic(),
            changed_files=int(result.get("changed_files") or 0),
            changed_lines=int(result.get("changed_lines") or 0),
        )
        self._emit(
            job,
            "candidate.confirmed",
            patch_hash=job.patch_hash,
            changed_files=confirmed.changed_files,
            changed_lines=confirmed.changed_lines,
            policy=self.winner_policy,
        )
        if self.winner_policy != FIRST_CONFIRMED:
            with self._lock:
                self._confirmed.append((job, confirmed, result))
            return
        self._declare(job, confirmed, result, None)

    def _declare(
        self,
        job: VerificationJob,
        confirmed: ConfirmedCandidate,
        result: dict[str, Any],
        policy_decision: PolicyDecision | None,
    ) -> None:
        decision = self.arbiter.declare_winner(
            job.run_id,
            job.candidate_id,
            job.patch_hash,
            job.base_commit,
            confirmed.evidence,
        )
        status = str(decision.get("status") or "")
        if status != "won":
            self._record(
                job,
                {
                    **result,
                    "status": status or "rejected",
                    "arbiter": decision,
                    "reason": decision.get("reason") or "arbiter_rejected",
                },
            )
            if status not in {"confirmed_but_not_selected"}:
                self._emit(
                    job,
                    "candidate.rejected",
                    stage="arbiter",
                    reason=str(decision.get("reason") or "arbiter_rejected"),
                    patch_hash=job.patch_hash,
                )
            return

        winner = {
            "candidate_id": job.candidate_id,
            "agent_id": job.agent_id,
            "patch_hash": job.patch_hash,
            "base_commit": job.base_commit,
            "patch_path": str(job.patch_path),
            "evidence": confirmed.evidence,
            "verification": result,
            "winner_policy": policy_decision.policy if policy_decision else self.winner_policy,
            "policy_reason": policy_decision.reason if policy_decision else "earliest_confirmation",
        }
        with self._lock:
            self._winner = winner
            if policy_decision is not None:
                self._policy_decision = policy_decision
        self._winner_event.set()
        if self.intake is not None:
            self.intake.close()
        self._record(
            job,
            {
                **result,
                "status": "won",
                "arbiter": decision,
                "policy": winner["winner_policy"],
            },
        )
        if self.on_winner is not None:
            try:
                self.on_winner(dict(winner))
            except Exception:  # noqa: BLE001 - winner is already durable
                logger.exception(
                    "winner_callback_failed",
                    extra={
                        "run_id": job.run_id,
                        "candidate_id": job.candidate_id,
                    },
                )

    def _emit_policy_decision(self, decision: PolicyDecision) -> None:
        if not self._confirmed:
            return
        job = self._confirmed[0][0]
        self._emit(job, "arbiter.policy_decision", **decision.as_dict())

    def _record(self, job: VerificationJob, result: dict[str, Any]) -> None:
        record = {
            "candidate_id": job.candidate_id,
            "agent_id": job.agent_id,
            "patch_hash": job.patch_hash,
            **result,
        }
        with self._lock:
            self._results.append(record)

    def _emit(self, job: VerificationJob, event: str, **data: Any) -> None:
        try:
            self.events.append_atomic(
                event,
                run_id=job.run_id,
                agent_id=job.agent_id,
                late_check=True,
                candidate_id=job.candidate_id,
                **data,
            )
        except Exception:  # noqa: BLE001 - best-effort telemetry
            logger.warning(
                "verification_event_persist_failed",
                extra={
                    "event": event,
                    "run_id": job.run_id,
                    "candidate_id": job.candidate_id,
                },
            )
