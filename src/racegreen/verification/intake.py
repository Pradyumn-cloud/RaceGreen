"""Candidate intake, freezing, deduplication."""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from pathlib import Path

from ..agent.candidate import CandidateSnapshot, snapshot_candidate, verify_candidate_snapshot
from ..agent.events import EventStore
from .gitutil import snapshot_patch_stable
from .prefilter import (
    TEST_CONFIG_FILES,
    PreFilter,
    PreFilterRejection,
    PreFilterResult,
    is_test_path,
)


@dataclass
class IntakeResult:
    accepted: bool
    snapshot: CandidateSnapshot | None
    rejections: tuple[object, ...] = ()


def _candidate_filename(candidate_id: str) -> str:
    """Return a safe, collision-resistant filename for an agent-supplied ID."""
    digest = hashlib.sha256(candidate_id.encode("utf-8")).hexdigest()[:12]
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in candidate_id)
    safe = safe[:80]
    # Always include the digest. This prevents traversal, Windows device-name
    # collisions, and accidental collisions between sanitized IDs.
    return f"{safe or 'candidate'}-{digest}"


class CandidateIntake:
    def __init__(
        self,
        prefilter: PreFilter | None = None,
        events: EventStore | None = None,
        *,
        enabled: bool = True,
    ) -> None:
        self.prefilter = prefilter or PreFilter()
        self.enabled = enabled
        self.events = events
        self._lock = threading.RLock()
        self._closed = False
        self._seen_hashes: set[str] = set()
        self._hash_attributions: dict[str, list[tuple[str, str, str]]] = {}  # hash -> [(run_id,agent_id,candidate_id)]
        self._candidate_owners: dict[str, str] = {}

    def close(self) -> None:
        """Stop accepting new candidates; used once a winner is confirmed."""
        with self._lock:
            self._closed = True

    @property
    def is_closed(self) -> bool:
        with self._lock:
            return self._closed

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
        if is_terminal or self._closed:
            if self.events is not None:
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

        requested_candidate_id = candidate_id
        with self._lock:
            if candidate_id in self._candidate_owners:
                # Candidate IDs are agent-supplied and may legitimately repeat
                # across agents (for example, two agents submitting the same
                # patch). Keep every snapshot addressable with a unique ID.
                base = f"{agent_id}:{candidate_id}"
                candidate_id = base
                suffix = 2
                while candidate_id in self._candidate_owners:
                    candidate_id = f"{base}:{suffix}"
                    suffix += 1
            self._candidate_owners[candidate_id] = agent_id

        if self.events is not None:
            try:
                self.events.append_atomic(
                    "candidate_frozen",
                    run_id=run_id,
                    agent_id=agent_id,
                    late_check=True,
                    candidate_id=candidate_id,
                    requested_candidate_id=requested_candidate_id,
                    base_commit=base_commit,
                )
            except Exception:
                pass

        # Generate stable patch. The logical candidate ID is retained for
        # arbitration, while the filename is made safe for the host filesystem.
        dest = candidate_dir / f"{_candidate_filename(candidate_id)}.patch"
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

        if not patch_bytes.strip():
            rejection = PreFilterRejection(
                "empty_patch",
                "candidate produced no patch content",
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
                except Exception:
                    pass
            return IntakeResult(False, None, (rejection,))

        # deduplicate
        with self._lock:
            if patch_hash in self._seen_hashes:
                self._hash_attributions.setdefault(patch_hash, []).append((run_id, agent_id, candidate_id))
                # record attribution but still may verify once; for now accept and attach attributions
            else:
                self._seen_hashes.add(patch_hash)
                self._hash_attributions[patch_hash] = [(run_id, agent_id, candidate_id)]

        # Verify the frozen patch is stable and applies to the recorded base
        # commit before any verification budget is spent.
        try:
            verify_candidate_snapshot(worktree, snap_res)
        except Exception as exc:
            rejection = PreFilterRejection(
                "snapshot_verification_failed",
                str(exc)[:500] or "candidate snapshot verification failed",
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
                except Exception:
                    pass
            return IntakeResult(False, None, (rejection,))

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

        if self.enabled:
            res = self.prefilter.filter(
                patch_bytes, patch_hash, candidate_id, worktree, base_commit
            )
        else:
            res = PreFilterResult(
                True,
                changed_paths=self.prefilter.changed_paths(patch_bytes),
            )
        if not res.changed_paths:
            rejection = PreFilterRejection(
                "empty_patch",
                "candidate produced no content change",
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
                except Exception:
                    pass
            return IntakeResult(False, None, (rejection,))
        if any(
            is_test_path(path)
            or path.replace("\\", "/").rsplit("/", 1)[-1] in TEST_CONFIG_FILES
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
