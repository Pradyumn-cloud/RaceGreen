from __future__ import annotations

import hashlib
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CandidateSnapshot:
    candidate_id: str
    patch_path: Path
    patch_hash: str
    base_commit: str


class CandidateVerificationError(RuntimeError):
    """Raised when an immutable candidate cannot be verified."""


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


def verify_candidate_snapshot(repository: Path, candidate: CandidateSnapshot) -> None:
    patch = candidate.patch_path.resolve(strict=True)
    if hashlib.sha256(patch.read_bytes()).hexdigest() != candidate.patch_hash:
        raise CandidateVerificationError("candidate patch hash changed")
    repository = repository.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="racegreen-verify-") as temporary:
        checkout = Path(temporary) / "checkout"
        clone = subprocess.run(
            ["git", "clone", "--no-hardlinks", str(repository), str(checkout)],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if clone.returncode != 0:
            raise CandidateVerificationError("unable to create verification checkout")
        checkout_result = subprocess.run(
            ["git", "-C", str(checkout), "checkout", "--detach", candidate.base_commit],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if checkout_result.returncode != 0:
            raise CandidateVerificationError("candidate base commit is unavailable")
        applied = subprocess.run(
            ["git", "-C", str(checkout), "apply", "--check", str(patch)],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if applied.returncode != 0:
            raise CandidateVerificationError(
                f"candidate patch does not apply: {applied.stderr.strip()}"
            )
