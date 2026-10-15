"""Fetch a pinned external benchmark repository into the ignored .bench tree."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from urllib.parse import urlparse


def _safe_destination(root: Path, name: str) -> Path:
    if not name or Path(name).name != name or name in {".", ".."}:
        raise ValueError("destination must be a single directory name")
    destination = (root / name).resolve()
    if root.resolve() not in destination.parents:
        raise ValueError("destination escapes .bench")
    return destination


def fetch(url: str, name: str, bench_root: Path = Path(".bench")) -> Path:
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "ssh", "git+ssh"}:
        raise ValueError("repository URL must use HTTPS or SSH")
    destination = _safe_destination((bench_root / "external").resolve(), name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"destination already exists: {destination}")
    command = ["git", "clone", "--no-tags", url, str(destination)]
    subprocess.run(command, check=True, cwd=bench_root.resolve())
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("name")
    parser.add_argument("--bench-root", type=Path, default=Path(".bench"))
    args = parser.parse_args()
    fetch(args.url, args.name, args.bench_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
