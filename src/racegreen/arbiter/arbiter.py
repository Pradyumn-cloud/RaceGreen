"""Core arbitration with a durable compare-and-set winner decision."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..agent.events import EventStore
from ..storage.db import RaceDB
from ..verification.evidence import VerificationEvidence
from ..verification.winner import WinnerSlot


class Arbiter:
    def __init__(
        self,
        db: RaceDB,
        events: EventStore,
        winner_slot: WinnerSlot | None = None,
        run_dir: Path | None = None,
    ) -> None:
        self.db = db
        self.events = events
        self.winner_slot = winner_slot or WinnerSlot()
        self.run_dir = run_dir
        self._intake_closed = False
        self.unverifiable = False

    def declare_winner(
        self,
        run_id: str,
        candidate_id: str,
        patch_hash: str,
        base_commit: str,
        evidence: VerificationEvidence | dict[str, Any],
    ) -> dict[str, Any]:
        if isinstance(evidence, VerificationEvidence):
            ev: dict[str, Any] = dict(evidence.__dict__)
        else:
            ev = dict(evidence)

        # A winner must be backed by a complete full-suite confirmation whose
        # identity matches the frozen candidate. These checks are deliberately
        # enforced here as well as in the verifier so a caller cannot skip them.
        if ev.get("complete_full_suite") is not True:
            return {"status": "rejected", "reason": "incomplete_full_suite"}
        if str(ev.get("patch_hash") or "") != str(patch_hash):
            return {"status": "rejected", "reason": "evidence_patch_hash_mismatch"}
        if str(ev.get("base_commit") or "") != str(base_commit):
            return {"status": "rejected", "reason": "evidence_base_commit_mismatch"}
        if str(ev.get("candidate_id") or "") != str(candidate_id):
            return {"status": "rejected", "reason": "evidence_candidate_id_mismatch"}

        ok = self.db.try_set_winner(run_id, candidate_id, patch_hash, base_commit, ev)
        if not ok:
            winner = self.db.get_winner(run_id)
            if winner and winner.get("candidate_id") != candidate_id:
                try:
                    self.events.append_atomic(
                        "candidate.confirmed_but_not_selected",
                        run_id=run_id,
                        agent_id="arbiter",
                        late_check=True,
                        candidate_id=candidate_id,
                        patch_hash=patch_hash,
                        reason="existing_winner",
                    )
                except Exception:
                    pass
                return {
                    "status": "confirmed_but_not_selected",
                    "existing_winner": winner,
                }
            return {"status": "failed", "reason": "cas_failed"}

        # CAS succeeded. Close intake before writing anything else so a late
        # candidate cannot be accepted while the winner is being announced.
        self._intake_closed = True
        self.winner_slot.try_set_winner(
            {
                "candidate_id": candidate_id,
                "patch_hash": patch_hash,
                "base_commit": base_commit,
                "evidence": ev,
            }
        )
        try:
            self.events.mark_terminal()
            self.events.append_atomic(
                "arbiter.winner_declared",
                run_id=run_id,
                agent_id="arbiter",
                late_check=False,
                event_id=f"{run_id}:winner:{candidate_id}",
                candidate_id=candidate_id,
                patch_hash=patch_hash,
                base_commit=base_commit,
            )
            self.events.append_atomic(
                "candidate.won",
                run_id=run_id,
                agent_id="arbiter",
                late_check=False,
                event_id=f"{run_id}:candidate-won:{candidate_id}",
                candidate_id=candidate_id,
                patch_hash=patch_hash,
                base_commit=base_commit,
                evidence=ev,
            )
            self.events.append_atomic(
                "arbiter.intake_closed",
                run_id=run_id,
                agent_id="arbiter",
                late_check=False,
                winner_candidate_id=candidate_id,
            )
        except Exception:
            # The winner row exists but the trace cannot be completed. The
            # scheduler must stop and mark the run unverifiable.
            self.unverifiable = True
            try:
                self.events.append_atomic(
                    "run.unverifiable",
                    run_id=run_id,
                    agent_id="arbiter",
                    late_check=False,
                    reason="event_store_failure_after_winner_cas",
                )
            except Exception:
                pass
            return {"status": "unverifiable", "reason": "event_store_failure"}
        return {"status": "won", "candidate_id": candidate_id}

    def close_intake(self) -> None:
        self._intake_closed = True

    @property
    def intake_closed(self) -> bool:
        return self._intake_closed

    def has_winner(self, run_id: str) -> bool:
        return self.db.get_winner(run_id) is not None
