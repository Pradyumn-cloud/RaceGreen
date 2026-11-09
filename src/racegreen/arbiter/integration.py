"""Safe export, branch creation, and explicit patch application."""

from __future__ import annotations

import hashlib
import subprocess
import tempfile
from pathlib import Path

from ..agent.events import EventStore


class IntegrationManager:
    def __init__(self, events: EventStore | None = None) -> None:
        self.events = events

    def export_patch_and_evidence(
        self,
        run_dir: Path,
        run_id: str,
        winner_info: dict[str, object],
        patch_path: Path,
        base_commit: str,
        task: str = "",
    ) -> dict[str, object]:
        out_patch = run_dir / "winner.patch"
        data = patch_path.resolve(strict=True).read_bytes()
        if hashlib.sha256(data).hexdigest() != winner_info.get("patch_hash"):
            return {"status": "refused", "reason": "tampered_patch"}
        out_patch.write_bytes(data)
        evidence_dir = run_dir / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        summary = evidence_dir / "summary.md"
        summary.write_text(
            f"# Run {run_id}\n\nBase: {base_commit}\n"
            f"Winner: {winner_info.get('candidate_id')}\nPatch: {out_patch}\n"
            f"Task: {task}\n",
            encoding="utf-8",
        )
        if self.events:
            self.events.append_atomic(
                "integration.patch_exported",
                run_id=run_id,
                agent_id="arbiter",
                late_check=False,
                patch_path=str(out_patch),
            )
        return {"status": "ok", "patch": str(out_patch), "summary": str(summary)}

    def create_branch(
        self,
        repo: Path,
        run_id: str,
        winner_info: dict[str, object],
        base_commit: str,
        tree_sha: str | None = None,
        patch_path: Path | None = None,
    ) -> dict[str, object]:
        """Create a review branch whose tree contains the verified winner patch."""
        if patch_path is None:
            return {"status": "refused", "reason": "winner patch is required"}
        try:
            patch = patch_path.resolve(strict=True)
            if hashlib.sha256(patch.read_bytes()).hexdigest() != winner_info.get("patch_hash"):
                return {"status": "refused", "reason": "tampered_patch"}
            repo = repo.resolve(strict=True)
            with tempfile.TemporaryDirectory(prefix="racegreen-branch-") as temporary:
                checkout = Path(temporary) / "checkout"
                added = self._run(["git", "-C", str(repo), "worktree", "add", "--detach", str(checkout), base_commit])
                if added.returncode != 0:
                    return {"status": "error", "reason": added.stderr.strip()}
                try:
                    applied = self._run(["git", "-C", str(checkout), "apply", "--check", str(patch)])
                    if applied.returncode != 0:
                        return {"status": "conflict", "reason": applied.stderr.strip()}
                    applied = self._run(["git", "-C", str(checkout), "apply", str(patch)])
                    if applied.returncode != 0:
                        return {"status": "conflict", "reason": applied.stderr.strip()}
                    staged = self._run(["git", "-C", str(checkout), "add", "--all"])
                    if staged.returncode != 0:
                        return {"status": "error", "reason": staged.stderr.strip()}
                    tree = self._run(["git", "-C", str(checkout), "write-tree"])
                    if tree.returncode != 0:
                        return {"status": "error", "reason": tree.stderr.strip()}
                    message = f"racegreen {run_id} winner {winner_info.get('candidate_id')}"
                    commit_result = self._run(
                        ["git", "-C", str(repo), "commit-tree", tree.stdout.strip(), "-p", base_commit, "-m", message]
                    )
                    if commit_result.returncode != 0:
                        return {"status": "error", "reason": commit_result.stderr.strip()}
                    commit = commit_result.stdout.strip()
                finally:
                    self._run(["git", "-C", str(repo), "worktree", "remove", "--force", str(checkout)])
            ref = f"refs/heads/racegreen/{run_id}"
            updated = self._run(["git", "-C", str(repo), "update-ref", ref, commit])
            if updated.returncode != 0:
                return {"status": "error", "reason": updated.stderr.strip()}
            return {"status": "ok", "branch": ref, "commit": commit}
        except (OSError, subprocess.SubprocessError) as exc:
            return {"status": "error", "reason": str(exc)}

    def apply_patch(
        self,
        repo: Path,
        patch_path: Path,
        base_commit: str,
        expected_patch_hash: str | None = None,
    ) -> dict[str, object]:
        """Apply only when the target is still at the candidate's captured base."""
        try:
            repo = repo.resolve(strict=True)
            patch = patch_path.resolve(strict=True)
            if expected_patch_hash and hashlib.sha256(patch.read_bytes()).hexdigest() != expected_patch_hash:
                return {"status": "refused", "reason": "tampered_patch"}
            head = self._run(["git", "-C", str(repo), "rev-parse", "HEAD"])
            if head.returncode != 0:
                return {"status": "error", "reason": head.stderr.strip()}
            if head.stdout.strip() != base_commit:
                return {"status": "stale_target", "reason": "target HEAD changed since the run started"}
            status = self._run(["git", "-C", str(repo), "status", "--porcelain"])
            if status.returncode != 0:
                return {"status": "error", "reason": status.stderr.strip()}
            if status.stdout.strip():
                return {
                    "status": "dirty_target",
                    "reason": "target working tree has uncommitted changes",
                }
            checked = self._run(["git", "-C", str(repo), "apply", "--check", str(patch)])
            if checked.returncode != 0:
                return {"status": "conflict", "reason": checked.stderr.strip()}
            applied = self._run(["git", "-C", str(repo), "apply", str(patch)])
            if applied.returncode != 0:
                return {"status": "conflict", "reason": applied.stderr.strip()}
            return {"status": "applied"}
        except (OSError, subprocess.SubprocessError) as exc:
            return {"status": "error", "reason": str(exc)}

    @staticmethod
    def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(args, check=False, capture_output=True, text=True)
