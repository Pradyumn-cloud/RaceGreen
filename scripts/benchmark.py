"""Run the Tier 1 benchmark with deterministic replayable candidate responses.

Two engines are available:

* ``pipeline`` (default for the CLI) runs the real ``RaceScheduler``,
  candidate intake, ``VerificationRunner``, and ``Arbiter``. The replay
  provider applies the checked-in patch through the agent tool loop, so the
  recorded winner is backed by a real full-suite confirmation.
* ``fixture`` is the older direct apply-and-run loop. It remains useful for
  fast protocol/plumbing checks and is not evidence for RaceGreen's behavior.

Provider integrations can supply a JSONL response recording with one patch per
task and arm; recordings are treated as immutable experiment inputs.
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
SRC = ROOT / "src"
for entry in (ROOT, SRC):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from benchmarks.tier1.generate import build_tasks, materialize_task  # noqa: E402, I001
from racegreen.agent.events import EventStore  # noqa: E402
from racegreen.agent.models import ProviderResponse, ToolCall  # noqa: E402
from racegreen.scheduler.budgets import RunBudgets  # noqa: E402
from racegreen.scheduler.runner import RaceScheduler  # noqa: E402
from racegreen.storage.db import RaceDB  # noqa: E402
from racegreen.verification.config import VerificationConfig  # noqa: E402


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


def _temporary_benchmark_root() -> Path:
    root = ROOT / ".bench" / "tmp"
    root.mkdir(parents=True, exist_ok=True)
    return root


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


class _ReplayProvider:
    """Replay one immutable patch through the normal agent tool loop.

    Using the real tool loop matters: the patch is applied inside the agent's
    worktree, frozen by candidate intake, and verified from that snapshot. The
    benchmark therefore exercises the same boundaries as a live provider.
    """

    def __init__(self, patch: Path | None, candidate_id: str) -> None:
        self.patch = patch
        self.candidate_id = candidate_id
        self._applied = False

    def complete(
        self, messages: list[dict[str, str]], cancel_event: Any = None
    ) -> ProviderResponse:
        del messages, cancel_event
        if self.patch is None:
            return ProviderResponse(content="No replay patch is available for this task and arm.")
        if not self._applied:
            self._applied = True
            return ProviderResponse(
                tool_calls=(
                    ToolCall(
                        "command",
                        {"argv": ["git", "apply", "--whitespace=nowarn", str(self.patch)]},
                    ),
                )
            )
        return ProviderResponse(
            tool_calls=(
                ToolCall("submit_candidate", {"candidate_id": self.candidate_id}),
            )
        )


def _portable_command(command: list[str]) -> list[str]:
    """Run task commands with the interpreter executing the benchmark."""
    return [sys.executable if part == "python" else part for part in command]


def _record(
    *,
    task: dict[str, Any],
    arm: str,
    repeat: int,
    model: str,
    provider: str,
    responses: Path | None,
    patch_hash: str | None,
    candidate_count: int,
    selected_passed: bool,
    winner_declared: bool,
    held_out_passed: bool,
    full_runs: int,
    started: float,
) -> dict[str, Any]:
    return {
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


def _pipeline_held_out_passed(
    task: dict[str, Any],
    *,
    patch_path: Path,
    root: Path,
    tag: str,
    timeout: float,
) -> bool:
    """Apply a confirmed winner to a clean task containing held-out tests."""
    held_root = root / f"{tag}-held"
    try:
        materialize_task(task, held_root)
        if not _apply_patch(held_root, patch_path):
            return False
        passed, _ = _test(
            held_root, _portable_command(list(task["commands"]["held_out"])), timeout
        )
        return passed
    finally:
        shutil.rmtree(held_root, ignore_errors=True)


def _winner_patch_path(
    summary: dict[str, Any], winner: dict[str, Any], database: Path
) -> Path | None:
    run_id = summary.get("run_id")
    candidate_id = winner.get("candidate_id")
    if not run_id or not candidate_id:
        return None
    try:
        db = RaceDB(database)
        events_path = summary.get("events_path")
        if events_path:
            db.ingest_events(EventStore(Path(str(events_path))).replay())
        raw = db.candidate_patch_path(str(run_id), str(candidate_id))
    except Exception:
        return None
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_file() else None


def _run_pipeline_case(
    *,
    task: dict[str, Any],
    arm: str,
    repeat: int,
    root: Path,
    recordings: dict[tuple[str, str], str | None],
    model: str,
    provider: str,
    responses: Path | None,
    timeout: float,
) -> dict[str, Any]:
    started = time.monotonic()
    task_id = str(task["task_id"])
    tag = f"{task_id}-{repeat}-{arm}"
    task_root = root / f"{tag}-pipeline"
    artifacts_root = root / f"{tag}-artifacts"
    database = root / f"{tag}.db"

    # The verifier must not see held-out tests. Materialize a visible-only
    # repository for the race, then evaluate the winner against a fresh copy
    # that includes the held-out suite.
    visible_task = dict(task)
    visible_task["_include_held_out"] = False
    materialize_task(visible_task, task_root)

    patch_value = recordings.get((task_id, arm), _response_patch(task, arm))
    patch = Path(patch_value) if patch_value else None
    patch_hash = (
        hashlib.sha256(patch.read_bytes()).hexdigest()
        if patch is not None and patch.is_file()
        else None
    )
    candidate_count = _candidate_count(arm)
    oracle = ROOT / "benchmarks" / "tier1" / str(task["oracle"]["patch"])

    def factory(agent_id: str) -> _ReplayProvider:
        selected = patch
        if (
            arm == "racegreen"
            and bool(task.get("control"))
            and candidate_count > 1
            and agent_id.endswith("2")
            and oracle.is_file()
        ):
            selected = oracle
        return _ReplayProvider(selected, candidate_id=f"{tag}-{agent_id}")

    config = VerificationConfig(
        full_suite_command=_portable_command(list(task["commands"]["full"])),
        test_command=_portable_command(list(task["commands"]["visible"])),
        use_selection=arm not in {"no_selection", "full_suite_only", "no_prefilter"},
        use_prefilter=arm != "no_prefilter",
        max_full_suite_runs_per_run=10,
        max_parallel_verifications=max(1, candidate_count),
        timeout_full_seconds=timeout,
        baseline_timeout_seconds=timeout,
    )
    max_concurrency = 1 if arm == "sequential_retry" else candidate_count
    winner_policy = (
        "best_of_confirmed"
        if arm in {"parallel_wait_all", "wait_all_no_cancel"}
        else "first_confirmed"
    )
    budgets = RunBudgets(
        max_wall_time_seconds=max(60.0, timeout * 4),
        max_agent_wall_time_seconds=max(30.0, timeout),
        max_candidates_total=max(candidate_count, 2),
        max_tool_calls_per_agent=20,
        max_output_bytes_per_agent=1_000_000,
    )

    try:
        try:
            scheduler = RaceScheduler(
                task_root,
                str(task["prompt"]),
                agents=candidate_count,
                max_concurrency=max_concurrency,
                artifacts_root=artifacts_root,
                budgets=budgets,
                database=database,
                verification_config=config,
                provider_factory=factory,
                winner_policy=winner_policy,
            )
            summary = scheduler.run()
        except Exception as exc:  # noqa: BLE001 - record infrastructure failure
            summary = {
                "state": "failed",
                "winner": None,
                "verification": [],
                "events_path": None,
                "error": str(exc),
            }

        events: list[dict[str, Any]] = []
        events_path = summary.get("events_path")
        if events_path:
            try:
                events = EventStore(Path(str(events_path))).replay()
            except Exception:
                events = []
        full_runs = sum(
            1
            for event in events
            if event.get("event") == "verification_finished"
            and event.get("stage") == "full"
        )
        selected_passed = any(
            event.get("event") == "prefilter_finished" and event.get("accepted") is True
            for event in events
        )
        winner = summary.get("winner")
        winner_declared = bool(winner) and summary.get("state") == "completed"
        held_out_passed = False
        if winner_declared and isinstance(winner, dict):
            patch_path = _winner_patch_path(summary, winner, database)
            if patch_path is not None:
                held_out_passed = _pipeline_held_out_passed(
                    task,
                    patch_path=patch_path,
                    root=root,
                    tag=tag,
                    timeout=timeout,
                )
        return _record(
            task=task,
            arm=arm,
            repeat=repeat,
            model=model,
            provider=provider,
            responses=responses,
            patch_hash=(
                str(winner["patch_hash"])
                if winner_declared and isinstance(winner, dict) and winner.get("patch_hash")
                else patch_hash
            ),
            candidate_count=candidate_count,
            selected_passed=selected_passed,
            winner_declared=winner_declared,
            held_out_passed=held_out_passed,
            full_runs=full_runs,
            started=started,
        )
    finally:
        shutil.rmtree(task_root, ignore_errors=True)
        shutil.rmtree(artifacts_root, ignore_errors=True)


def _run_pipeline_benchmark(
    *,
    tasks: list[dict[str, Any]],
    repeats: int,
    output: Path,
    recordings: dict[tuple[str, str], str | None],
    model: str,
    provider: str,
    responses: Path | None,
    timeout: float,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    with output.open("w", encoding="utf-8") as stream, tempfile.TemporaryDirectory(
        prefix="racegreen-bench-", dir=_temporary_benchmark_root()
    ) as temporary:
        root = Path(temporary)
        for repeat in range(repeats):
            for task in tasks:
                for arm in ARMS:
                    record = _run_pipeline_case(
                        task=task,
                        arm=arm,
                        repeat=repeat,
                        root=root,
                        recordings=recordings,
                        model=model,
                        provider=provider,
                        responses=responses,
                        timeout=timeout,
                    )
                    stream.write(json.dumps(record, sort_keys=True) + "\n")
                    stream.flush()
                    results.append(record)
    return results


def _run_fixture_benchmark(
    *,
    tasks: list[dict[str, Any]],
    repeats: int,
    output: Path,
    recordings: dict[tuple[str, str], str | None],
    model: str,
    provider: str,
    responses: Path | None,
    timeout: float,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    with output.open("w", encoding="utf-8") as stream, tempfile.TemporaryDirectory(
        prefix="racegreen-bench-", dir=_temporary_benchmark_root()
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
                    full_passed = False
                    held_out_passed = False
                    patch_hash = None
                    if patch is not None and patch.is_file():
                        patch_hash = hashlib.sha256(patch.read_bytes()).hexdigest()
                        selected_passed = _apply_patch(task_root, patch)
                        if selected_passed or arm == "full_suite_only":
                            full_runs = 1
                            full_passed, _ = _test(
                                task_root,
                                _portable_command(list(task["commands"]["full"])),
                                timeout,
                            )
                            if full_passed:
                                held_out_passed, _ = _test(
                                    task_root,
                                    _portable_command(list(task["commands"]["held_out"])),
                                    timeout,
                                )
                    # A candidate is only a winner when a full-suite run
                    # actually passed. Applying a patch or passing selected
                    # tests is insufficient.
                    winner_declared = full_runs > 0 and full_passed
                    record = _record(
                        task=task,
                        arm=arm,
                        repeat=repeat,
                        model=model,
                        provider=provider,
                        responses=responses,
                        patch_hash=patch_hash,
                        candidate_count=candidate_count,
                        selected_passed=selected_passed,
                        winner_declared=winner_declared,
                        held_out_passed=held_out_passed,
                        full_runs=full_runs,
                        started=started,
                    )
                    stream.write(json.dumps(record, sort_keys=True) + "\n")
                    stream.flush()
                    results.append(record)
                    shutil.rmtree(task_root, ignore_errors=True)
    return results


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
    engine: str = "fixture",
) -> list[dict[str, Any]]:
    tasks = [
        task
        for task in build_tasks()
        if task["split"] == split and (task_ids is None or str(task["task_id"]) in task_ids)
    ]
    recordings = _load_recordings(responses)
    output.parent.mkdir(parents=True, exist_ok=True)
    if engine == "pipeline":
        return _run_pipeline_benchmark(
            tasks=tasks,
            repeats=repeats,
            output=output,
            recordings=recordings,
            model=model,
            provider=provider,
            responses=responses,
            timeout=timeout,
        )
    if engine != "fixture":
        raise ValueError(f"unknown benchmark engine: {engine}")
    return _run_fixture_benchmark(
        tasks=tasks,
        repeats=repeats,
        output=output,
        recordings=recordings,
        model=model,
        provider=provider,
        responses=responses,
        timeout=timeout,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--responses", type=Path)
    parser.add_argument("--model", default=os.environ.get("RACEGREEN_MODEL", "offline-replay"))
    parser.add_argument("--provider", default=os.environ.get("RACEGREEN_PROVIDER_URL", "offline"))
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument(
        "--engine",
        choices=("pipeline", "fixture"),
        default="pipeline",
        help="Use the real scheduler/verifier/arbiter pipeline (default) or the fast fixture loop.",
    )
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
        engine=args.engine,
    )
    print(json.dumps({"records": len(records), "output": str(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
