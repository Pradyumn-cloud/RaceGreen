from __future__ import annotations

import hashlib
import os
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


def compute_patch_hash(patch_bytes: bytes) -> str:
    return hashlib.sha256(patch_bytes).hexdigest()


def snapshot_candidate(
    worktree: Path, destination: Path, *, candidate_id: str, base_commit: str
) -> CandidateSnapshot:
    worktree = worktree.resolve(strict=True)
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [
            "git",
            "-C",
            str(worktree),
            "-c",
            "core.quotePath=false",
            "diff",
            "--binary",
            "--full-index",
            "--no-color",
            "--no-ext-diff",
            "--no-textconv",
            "--no-renames",
            "--ignore-cr-at-eol",
            base_commit,
            "--",
        ],
        check=False,
        capture_output=True,
        text=False,
        env=env,
    )
    if result.returncode != 0 and result.returncode != 1:
        raise RuntimeError(f"Unable to snapshot candidate {candidate_id}")
    patch = result.stdout.replace(b"\r\n", b"\n")
    patch_hash = compute_patch_hash(patch)
    tmp = destination.with_suffix(destination.suffix + ".tmp")
    tmp.write_bytes(patch)
    os.replace(tmp, destination)
    return CandidateSnapshot(candidate_id, destination, patch_hash, base_commit)


def verify_candidate_snapshot(repository: Path, candidate: CandidateSnapshot) -> None:
    patch = candidate.patch_path.resolve(strict=True)
    data = patch.read_bytes()
    if not data.strip():
        raise CandidateVerificationError("candidate patch is empty")
    if compute_patch_hash(data) != candidate.patch_hash:
        raise CandidateVerificationError("candidate patch hash changed")
    repository = repository.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="racegreen-verify-") as temporary:
        checkout = Path(temporary) / "checkout"
        clone = subprocess.run(
            [
                "git",
                "-c",
                "core.autocrlf=false",
                "-c",
                "core.eol=lf",
                "clone",
                "--no-checkout",
                "--no-hardlinks",
                str(repository),
                str(checkout),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if clone.returncode != 0:
            raise CandidateVerificationError("unable to create verification checkout")
        checkout_result = subprocess.run(
            [
                "git",
                "-c",
                "core.autocrlf=false",
                "-c",
                "core.eol=lf",
                "-C",
                str(checkout),
                "checkout",
                "--detach",
                candidate.base_commit,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if checkout_result.returncode != 0:
            raise CandidateVerificationError("candidate base commit is unavailable")
        applied = subprocess.run(
            [
                "git",
                "-c",
                "core.autocrlf=false",
                "-c",
                "core.eol=lf",
                "-C",
                str(checkout),
                "apply",
                "--check",
                "--whitespace=nowarn",
                str(patch),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if applied.returncode != 0:
            raise CandidateVerificationError(
                f"candidate patch does not apply: {applied.stderr.strip()}"
            )
