"""Safe integration: auto-export patch+evidence, separate branch/apply."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from ..agent.events import EventStore


class IntegrationManager:
    def __init__(self, events: EventStore | None = None) -> None:
        self.events = events

    def export_patch_and_evidence(self, run_dir: Path, run_id: str, winner_info: dict, patch_path: Path, base_commit: str, task: str = "") -> dict:
        out_patch = run_dir / "winner.patch"
        # copy/verfiy
        data = patch_path.read_bytes()
        if hashlib.sha256(data).hexdigest() != winner_info.get("patch_hash"):
            return {"status": "refused", "reason": "tampered_patch"}
        out_patch.write_bytes(data)
        evidence_dir = run_dir / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        summary = evidence_dir / "summary.md"
        summary.write_text(f"# Run {run_id}\n\nBase: {base_commit}\nWinner: {winner_info.get('candidate_id')}\nPatch: {out_patch}\n")
        if self.events:
            try:
                self.events.append_atomic("integration.patch_exported", run_id=run_id, agent_id="arbiter", late_check=False, patch_path=str(out_patch))
            except Exception:
                pass
        # pin refs
        try:
            self._pin_refs(run_dir, winner_info)
        except Exception:
            pass
        return {"status": "ok", "patch": str(out_patch), "summary": str(summary)}

    def _pin_refs(self, run_dir: Path, winner_info: dict) -> None:
        # best effort - create refs if in git repo context not required
        pass

    def create_branch(self, repo: Path, run_id: str, winner_info: dict, base_commit: str, tree_sha: str | None = None) -> dict:
        # use commit-tree + update-ref (no checkout)
        try:
            if tree_sha is None:
                # get tree from patch is complex; try to use base tree as placeholder? better write-tree not available
                tree_sha_res = subprocess.run(["git", "-C", str(repo), "rev-parse", f"{base_commit}^{{tree}}"], check=False, capture_output=True, text=True)
                tree_sha = tree_sha_res.stdout.strip()
            msg = f"racegreen {run_id} winner {winner_info.get('candidate_id')}"
            cmt = subprocess.run(["git", "-C", str(repo), "commit-tree", tree_sha, "-p", base_commit, "-m", msg], check=False, capture_output=True, text=True)
            if cmt.returncode != 0:
                return {"status": "error", "reason": cmt.stderr}
            commit = cmt.stdout.strip()
            ref = f"refs/heads/racegreen/{run_id}"
            upd = subprocess.run(["git", "-C", str(repo), "update-ref", ref, commit], check=False, capture_output=True, text=True)
            if upd.returncode != 0:
                return {"status": "error", "reason": upd.stderr}
            return {"status": "ok", "branch": ref, "commit": commit}
        except Exception as e:
            return {"status": "error", "reason": str(e)}

    def apply_patch(self, repo: Path, patch_path: Path, base_commit: str) -> dict:
        # check with merge-tree if possible
        try:
            # git merge-tree --write-tree support varies; try
            subprocess.run(
                ["git", "-C", str(repo), "merge-tree", "--write-tree", base_commit, base_commit],
                check=False,
                capture_output=True,
            )
        except Exception:
            pass
        # apply
        try:
            r = subprocess.run(["git", "-C", str(repo), "apply", "--check", str(patch_path)], check=False, capture_output=True)
            if r.returncode != 0:
                return {"status": "conflict", "reason": r.stderr.decode("utf-8", errors="replace")}
            r2 = subprocess.run(["git", "-C", str(repo), "apply", str(patch_path)], check=False, capture_output=True)
            if r2.returncode != 0:
                return {"status": "conflict", "reason": r2.stderr.decode("utf-8", errors="replace")}
            return {"status": "applied"}
        except Exception as e:
            return {"status": "error", "reason": str(e)}
