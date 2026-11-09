from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from racegreen.arbiter.integration import IntegrationManager


def _repo(tmp_path: Path) -> tuple[Path, str, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "base"],
        check=True,
        capture_output=True,
    )
    base = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    patch = tmp_path / "winner.patch"
    patch.write_text(
        "diff --git a/app.py b/app.py\nindex 3f2a5a1..0000000 100644\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n",
        encoding="utf-8",
    )
    return repo, base, patch


def test_create_branch_contains_winner_patch(tmp_path: Path, monkeypatch) -> None:
    repo, base, patch = _repo(tmp_path)
    monkeypatch.setenv("GIT_AUTHOR_NAME", "RaceGreen")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "racegreen@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "RaceGreen")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "racegreen@example.com")
    patch_hash = hashlib.sha256(patch.read_bytes()).hexdigest()

    result = IntegrationManager().create_branch(
        repo, "run-1", {"candidate_id": "candidate-1", "patch_hash": patch_hash}, base, patch_path=patch
    )

    assert result["status"] == "ok"
    assert subprocess.run(
        ["git", "-C", str(repo), "show", f"{result['commit']}:app.py"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout == "value = 2\n"


def test_apply_patch_rejects_stale_target(tmp_path: Path) -> None:
    repo, base, patch = _repo(tmp_path)
    (repo / "app.py").write_text("value = 9\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "app.py"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "changed target",
        ],
        check=True,
        capture_output=True,
    )

    result = IntegrationManager().apply_patch(repo, patch, base)

    assert result["status"] == "stale_target"


def test_apply_patch_rejects_dirty_target(tmp_path: Path) -> None:
    repo, base, patch = _repo(tmp_path)
    (repo / "app.py").write_text("value = 3\n", encoding="utf-8")

    result = IntegrationManager().apply_patch(repo, patch, base)

    assert result["status"] == "dirty_target"
