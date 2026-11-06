"""Candidate intake, freezing, deduplication."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from ..agent.candidate import CandidateSnapshot, snapshot_candidate, verify_candidate_snapshot
from ..agent.events import EventStore
from .gitutil import snapshot_patch_stable
from .prefilter import PreFilter, PreFilterRejection


@dataclass
class IntakeResult:
    accepted: bool
    snapshot: CandidateSnapshot | None
    rejections: tuple[object, ...] = ()


class CandidateIntake:
    def __init__(self, prefilter: PreFilter | None = None, events: EventStore | None = None) -> None:
        self.prefilter = prefilter or PreFilter()
        self.events = events
        self._lock = threading.RLock()
        self._seen_hashes: set[str] = set()
        self._hash_attributions: dict[str, list[tuple[str, str, str]]] = {}  # hash -> [(run_id,agent_id,candidate_id)]

    def freeze_and_intake(
        self,
        worktree: Path,
        candidate_dir: Path,
        candidate_id: str,
        base_commit: str,
        run_id: str,
        agent_id: str,
        is_terminal: bool = False,
    ) -> IntakeResult:
        if is_terminal and self.events is not None:
            try:
                self.events.append_atomic(
                    "event.rejected_late",
                    run_id=run_id,
                    agent_id=agent_id,
                    late_check=False,
                    candidate_id=candidate_id,
                )
            except Exception:
                pass
            return IntakeResult(False, None)

        if self.events is not None:
            try:
                self.events.append_atomic("candidate_frozen", run_id=run_id, agent_id=agent_id, late_check=True, candidate_id=candidate_id, base_commit=base_commit)
            except Exception:
                pass

        # Generate stable patch
        dest = candidate_dir / f"{candidate_id}.patch"
        try:
            patch_bytes, patch_hash = snapshot_patch_stable(worktree, base_commit, dest)
        except Exception:
            # fallback
            snap = snapshot_candidate(worktree, dest, candidate_id=candidate_id, base_commit=base_commit)
            patch_bytes = snap.patch_path.read_bytes()
            patch_hash = snap.patch_hash
            snap_res = snap
        else:
            snap_res = CandidateSnapshot(candidate_id, dest, patch_hash, base_commit)

        # deduplicate
        with self._lock:
            if patch_hash in self._seen_hashes:
                self._hash_attributions.setdefault(patch_hash, []).append((run_id, agent_id, candidate_id))
                # record attribution but still may verify once; for now accept and attach attributions
            else:
                self._seen_hashes.add(patch_hash)
                self._hash_attributions[patch_hash] = [(run_id, agent_id, candidate_id)]

        # verify snapshot hash
        try:
            verify_candidate_snapshot(worktree, snap_res)
        except Exception:
            pass

        if self.events is not None:
            try:
                self.events.append_atomic(
                    "prefilter_started",
                    run_id=run_id,
                    agent_id=agent_id,
                    late_check=True,
                    candidate_id=candidate_id,
                    patch_hash=patch_hash,
                    patch_path=str(dest),
                )
            except Exception:
                pass

        res = self.prefilter.filter(patch_bytes, patch_hash, candidate_id, worktree, base_commit)
        if any(
            path == "tests" or path.startswith("tests/")
            for path in res.changed_paths
        ):
            rejection = PreFilterRejection(
                "test_file_change",
                "candidate modifies tests",
                patch_hash,
                candidate_id,
            )
            if self.events is not None:
                try:
                    self.events.append_atomic(
                        "candidate_rejected",
                        run_id=run_id,
                        agent_id=agent_id,
                        late_check=True,
                        candidate_id=candidate_id,
                        patch_hash=patch_hash,
                        patch_path=str(dest),
                        rule=rejection.rule,
                        evidence=rejection.evidence,
                    )
                except (OSError, RuntimeError):
                    pass
            return IntakeResult(False, None, (rejection,))
        if self.events is not None:
            try:
                self.events.append_atomic(
                    "prefilter_finished",
                    run_id=run_id,
                    agent_id=agent_id,
                    late_check=True,
                    candidate_id=candidate_id,
                    patch_hash=patch_hash,
                    patch_path=str(dest),
                    changed_paths=list(res.changed_paths),
                    accepted=res.accepted,
                    duration_ms=res.duration_ms,
                    soft_limit_triggered=res.soft_limit_triggered,
                    hard_cap_triggered=res.hard_cap_triggered,
                    excluded_paths_count=res.excluded_paths_count,
                )
            except Exception:
                pass
            if not res.accepted:
                for r in res.rejections:
                    try:
                        self.events.append_atomic(
                            "candidate_rejected",
                            run_id=run_id,
                            agent_id=agent_id,
                            late_check=True,
                            candidate_id=candidate_id,
                            patch_hash=patch_hash,
                            patch_path=str(dest),
                            rule=r.rule,
                            evidence=r.evidence,
                        )
                    except Exception:
                        pass
                return IntakeResult(False, None, res.rejections)
        return IntakeResult(True, snap_res, ())
