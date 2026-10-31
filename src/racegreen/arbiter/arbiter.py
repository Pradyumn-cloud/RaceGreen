"""Core arbitration with durable CAS."""

from __future__ import annotations

from pathlib import Path

from ..agent.events import EventStore
from ..storage.db import RaceDB
from ..verification.evidence import VerificationEvidence
from ..verification.winner import WinnerSlot


class Arbiter:
    def __init__(self, db: RaceDB, events: EventStore, winner_slot: WinnerSlot | None = None, run_dir: Path | None = None) -> None:
        self.db = db
        self.events = events
        self.winner_slot = winner_slot or WinnerSlot()
        self.run_dir = run_dir
        self._intake_closed = False

    def declare_winner(self, run_id: str, candidate_id: str, patch_hash: str, base_commit: str, evidence: VerificationEvidence | dict) -> dict:
        ev = evidence.__dict__ if hasattr(evidence, "__dict__") else dict(evidence)
        # durable CAS
        ok = self.db.try_set_winner(run_id, candidate_id, patch_hash, base_commit, ev)
        if not ok:
            # check if winner exists
            w = self.db.get_winner(run_id)
            if w and w.get("candidate_id") != candidate_id:
                try:
                    self.events.append_atomic("candidate.confirmed_but_not_selected", run_id=run_id, agent_id="arbiter", late_check=True, candidate_id=candidate_id, patch_hash=patch_hash, reason="existing_winner")
                except Exception:
                    pass
                return {"status": "confirmed_but_not_selected", "existing_winner": w}
            return {"status": "failed", "reason": "cas_failed"}
        # CAS succeeded
        self._intake_closed = True
        try:
            self.events.append_atomic("arbiter.winner_declared", run_id=run_id, agent_id="arbiter", late_check=False, candidate_id=candidate_id, patch_hash=patch_hash, base_commit=base_commit)
            self.events.append_atomic("arbiter.intake_closed", run_id=run_id, agent_id="arbiter", late_check=False, winner_candidate_id=candidate_id)
        except Exception as e:
            # durability failure -> unverifiable
            try:
                self.events.append_atomic("run.unverifiable", run_id=run_id, agent_id="arbiter", late_check=False, reason=f"event_store_failure:{e}")
            except Exception:
                pass
            raise
        return {"status": "won", "candidate_id": candidate_id}

    def close_intake(self) -> None:
        self._intake_closed = True
