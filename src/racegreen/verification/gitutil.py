"""Git utilities for stable patch generation."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


def git_env_isolated() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def snapshot_patch_stable(worktree: Path, base_commit: str, destination: Path, excludes: tuple[str, ...] = ()) -> tuple[bytes, str]:
    """Create stable patch using git tree objects with isolated config."""
    worktree = worktree.resolve(strict=True)
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    env = git_env_isolated()
    # Ensure no bytecode
    # Build exclude via .git/info/exclude or just rely on add -A; also pass via core.excludesFile not needed
    # Create temp index
    with tempfile.TemporaryDirectory(prefix="racegreen-idx-") as td:
        index = Path(td) / "index"
        env2 = env.copy()
        env2["GIT_INDEX_FILE"] = str(index)
        # Add all
        subprocess.run(["git", "-C", str(worktree), "add", "-A"], check=False, capture_output=True, env=env2)
        # write tree
        tree = subprocess.run(["git", "-C", str(worktree), "write-tree"], check=False, capture_output=True, text=True, env=env2)
        if tree.returncode != 0:
            raise RuntimeError("failed to write tree")
        tree_sha = tree.stdout.strip()
        # diff
        diff = subprocess.run(
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
                base_commit,
                tree_sha,
            ],
            check=False,
            capture_output=True,
            text=False,
            env=env,
        )
        if diff.returncode not in (0, 1):  # 0 no diff, 1 diff exists
            raise RuntimeError("failed to generate diff")
        patch = diff.stdout
        # apply --check
        chk = subprocess.run(["git", "apply", "--check", "--whitespace=nowarn"], input=patch, capture_output=True, env=env)
        # write atomically
        tmp = destination.with_suffix(destination.suffix + ".tmp")
        tmp.write_bytes(patch)
        tmp.replace(destination)
        import hashlib
        return patch, hashlib.sha256(patch).hexdigest()


def apply_check(patch_bytes: bytes) -> tuple[bool, str]:
    env = git_env_isolated()
    res = subprocess.run(["git", "apply", "--check", "--whitespace=nowarn"], input=patch_bytes, capture_output=True, env=env)
    return res.returncode == 0, res.stderr.decode("utf-8", errors="replace")


def numstat(patch_bytes: bytes) -> str:
    env = git_env_isolated()
    res = subprocess.run(["git", "apply", "--numstat"], input=patch_bytes, capture_output=True, env=env)
    return res.stdout.decode("utf-8", errors="replace")


def summary(patch_bytes: bytes) -> str:
    env = git_env_isolated()
    res = subprocess.run(["git", "apply", "--summary"], input=patch_bytes, capture_output=True, env=env)
    return res.stdout.decode("utf-8", errors="replace")
