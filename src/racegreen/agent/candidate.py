from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CandidateSnapshot:
    candidate_id: str
    patch_path: Path
    patch_hash: str
    base_commit: str


def snapshot_candidate(
    worktree: Path, destination: Path, *, candidate_id: str, base_commit: str
) -> CandidateSnapshot:
    worktree = worktree.resolve(strict=True)
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "-C", str(worktree), "diff", "--binary", base_commit, "--"],
        check=False,
        capture_output=True,
        text=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Unable to snapshot candidate {candidate_id}")
    patch = result.stdout
    patch_hash = hashlib.sha256(patch).hexdigest()
    destination.write_bytes(patch)
    return CandidateSnapshot(candidate_id, destination, patch_hash, base_commit)
