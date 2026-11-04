"""Run the Tier 1 benchmark with deterministic replayable candidate responses.

The default response source is the checked-in oracle/decoy patch set. This
validates the benchmark protocol without requiring a provider. Provider
integrations can supply a JSONL response recording with one patch per task and
arm; recordings are treated as immutable experiment inputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.tier1.generate import build_tasks, materialize_task  # noqa: E402, I001


ARMS = (
    "single",
    "sequential_retry",
    "parallel_wait_all",
    "racegreen",
    "no_selection",
    "no_prefilter",
    "wait_all_no_cancel",
    "full_suite_only",
)


def _run(command: list[str], cwd: Path, timeout: float = 120.0) -> tuple[int, float]:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return -9, time.monotonic() - started
    return completed.returncode, time.monotonic() - started


def _apply_patch(checkout: Path, patch: Path) -> bool:
    if not patch.is_file():
        return False
    check, _ = _run(["git", "apply", "--check", str(patch)], checkout)
    if check != 0:
        return False
    apply, _ = _run(["git", "apply", "--whitespace=nowarn", str(patch)], checkout)
    return apply == 0


def _test(checkout: Path, command: list[str], timeout: float) -> tuple[bool, float]:
    returncode, duration = _run(command, checkout, timeout)
    return returncode == 0, duration


def _fingerprint() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "git": subprocess.run(
            ["git", "--version"], capture_output=True, text=True, check=False
        ).stdout.strip(),
    }


def _response_patch(task: dict[str, Any], arm: str) -> str | None:
    """Return the replay patch relative to the repository root."""
    oracle = ROOT / "benchmarks" / "tier1" / str(task["oracle"]["patch"])
    decoys = task.get("decoy_patches", [])
    if task.get("control") and decoys and arm in {"single", "racegreen"}:
        return str(ROOT / "benchmarks" / "tier1" / str(decoys[0]))
    if task.get("control") and str(task["kind"]) in {"unsolvable", "control-baseline"}:
        return None
    return str(oracle)


def _load_recordings(path: Path | None) -> dict[tuple[str, str], str | None]:
    if path is None:
        return {}
    recordings: dict[tuple[str, str], str | None] = {}
    with path.open(encoding="utf-8-sig") as stream:
        for line in stream:
            if not line.strip():
                continue
            record = json.loads(line)
            key = (str(record["task_id"]), str(record["arm"]))
            patch = record.get("patch")
            recordings[key] = str(patch) if patch else None
    return recordings


def _candidate_count(arm: str) -> int:
    return 1 if arm in {"single", "full_suite_only"} else 2


def run_benchmark(
    *,
    split: str,
    repeats: int,
    output: Path,
    responses: Path | None = None,
    model: str = "offline-replay",
    provider: str = "offline",
    timeout: float = 120.0,
    task_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    tasks = [
        task
        for task in build_tasks()
        if task["split"] == split and (task_ids is None or str(task["task_id"]) in task_ids)
    ]
    recordings = _load_recordings(responses)
    output.parent.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    with output.open("w", encoding="utf-8") as stream, tempfile.TemporaryDirectory(
        prefix="racegreen-bench-"
    ) as temporary:
        root = Path(temporary)
        for repeat in range(repeats):
            for task in tasks:
                for arm in ARMS:
                    started = time.monotonic()
                    task_root = root / f"{task['task_id']}-{repeat}-{arm}"
                    materialize_task(task, task_root)
                    patch_value = recordings.get(
                        (str(task["task_id"]), arm), _response_patch(task, arm)
                    )
                    patch = Path(patch_value) if patch_value else None
                    candidate_count = _candidate_count(arm)
                    selected_passed = False
                    full_runs = 0
                    held_out_passed = False
                    patch_hash = None
                    if patch is not None and patch.is_file():
                        patch_hash = hashlib.sha256(patch.read_bytes()).hexdigest()
                        selected_passed = _apply_patch(task_root, patch)
                        if selected_passed or arm == "full_suite_only":
                            full_runs = 1
                            full_passed, _ = _test(
                                task_root, list(task["commands"]["full"]), timeout
                            )
                            if full_passed:
                                held_out_passed, _ = _test(
                                    task_root, list(task["commands"]["held_out"]), timeout
                                )
                    winner_declared = full_runs > 0 and selected_passed
                    record = {
                        "event": "task_result",
                        "arm": arm,
                        "task_id": task["task_id"],
                        "control": bool(task["control"]),
                        "split": task["split"],
                        "difficulty": task["difficulty"],
                        "stratum": task["stratum"],
                        "known_flaky": bool(task["known_flaky"]),
                        "repeat": repeat,
                        "model": model,
                        "provider": provider,
                        "run_date": time.strftime("%Y-%m-%d", time.gmtime()),
                        "protocol_version": str(task["schema_version"]),
                        "task_list_hash": task["task_list_hash"],
                        "environment": _fingerprint(),
                        "response_source": "recording" if responses else "oracle-replay",
                        "patch_hash": patch_hash,
                        "candidate_count": candidate_count,
                        "selected_passed": selected_passed,
                        "winner_declared": winner_declared,
                        "held_out_passed": held_out_passed if winner_declared else None,
                        "resolved": winner_declared and (held_out_passed or bool(task["control"])),
                        "false_winner": winner_declared and not held_out_passed,
                        "time_to_resolve_seconds": (
                            time.monotonic() - started if winner_declared and held_out_passed else None
                        ),
                        "full_suite_runs": full_runs,
                        "full_suite_runs_avoided": max(candidate_count - full_runs, 0),
                        "duration_seconds": time.monotonic() - started,
                    }
                    stream.write(json.dumps(record, sort_keys=True) + "\n")
                    stream.flush()
                    results.append(record)
                    shutil.rmtree(task_root, ignore_errors=True)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--responses", type=Path)
    parser.add_argument("--model", default=os.environ.get("RACEGREEN_MODEL", "offline-replay"))
    parser.add_argument("--provider", default=os.environ.get("RACEGREEN_PROVIDER_URL", "offline"))
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--task", dest="task_ids", action="append", help="Run only this task ID")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    records = run_benchmark(
        split=args.split,
        repeats=args.repeats,
        output=args.output,
        responses=args.responses,
        model=args.model,
        provider=args.provider,
        timeout=args.timeout,
        task_ids=set(args.task_ids) if args.task_ids else None,
    )
    print(json.dumps({"records": len(records), "output": str(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
