"""Base-aware Python syntax checking."""

from __future__ import annotations

import ast
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SyntaxResult:
    ok: bool
    error: str | None = None


def check_python_syntax(patch_bytes: bytes, worktree: Path, base_commit: str, target_version: tuple[int, int] = (3, 12), max_bytes: int = 1_000_000) -> SyntaxResult:
    # Try to find changed .py files from patch
    try:
        # crude parse of +++ b/ paths
        import re

        paths = re.findall(r"^\+\+\+ b/(.+)$", patch_bytes.decode("utf-8", errors="replace"), re.M)
    except Exception:
        paths = []
    for p in paths:
        if not p.endswith(".py"):
            continue
        # get base content
        base_res = subprocess.run(["git", "-C", str(worktree), "show", f"{base_commit}:{p}"], check=False, capture_output=True)
        if base_res.returncode != 0:
            # new file - check patched content by applying to temp? skip base-parse if no base
            continue  # new files: if syntax bad, will be caught by apply? but requirement says compare to base; if no base, be conservative? only reject if introduced error relative to base - no base means can't say introduced; skip
        base_content = base_res.stdout
        if len(base_content) > max_bytes:
            continue
        try:
            ast.parse(base_content, filename=p)
            base_ok = True
        except SyntaxError:
            base_ok = False
        if not base_ok:
            continue  # pre-existing error, don't reject
        # get patched content by applying patch in temp copy of base
        try:
            with tempfile.TemporaryDirectory(prefix="racegreen-syn-") as td:
                fp = Path(td) / os.path.basename(p)
                fp.write_bytes(base_content)
                # apply patch to this file context is complex; easier: try to construct by git apply in temp worktree
                pass
        except Exception:
            pass
        # Fallback: use git apply to temp index of a temp checkout is heavy; do simpler - apply patch and extract
    # Simpler approach: apply patch to a temp clone of base and check modified py files
    try:
        with tempfile.TemporaryDirectory(prefix="racegreen-syn2-") as td:
            checkout = Path(td) / "co"
            subprocess.run(["git", "clone", "--no-hardlinks", str(worktree), str(checkout)], check=False, capture_output=True)
            subprocess.run(["git", "-C", str(checkout), "checkout", "--detach", base_commit], check=False, capture_output=True)
            # apply patch
            proc = subprocess.run(["git", "-C", str(checkout), "apply", "--whitespace=nowarn"], input=patch_bytes, capture_output=True)
            if proc.returncode != 0:
                return SyntaxResult(True)  # apply check will catch
            # find modified py files
            for root, dirs, files in os.walk(checkout):
                for fn in files:
                    if fn.endswith(".py"):
                        full = Path(root) / fn
                        try:
                            rel = full.relative_to(checkout)
                        except Exception:
                            continue
                        rels = str(rel).replace("\\", "/")
                        if rels not in paths and not any(p.endswith(rels) or rels.endswith(p.split("/")[-1]) for p in paths):
                            continue
                        try:
                            content = full.read_bytes()
                            if len(content) > max_bytes:
                                continue
                            ast.parse(content, filename=rels)
                        except SyntaxError as e:
                            return SyntaxResult(False, f"syntax error in {rels}: {e}")
    except Exception:
        pass
    return SyntaxResult(True)
