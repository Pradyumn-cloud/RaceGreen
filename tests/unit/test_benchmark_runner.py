from __future__ import annotations

import json
from pathlib import Path

from scripts.benchmark import ARMS, run_benchmark


def test_replay_benchmark_records_all_arms_and_protocol_metadata(tmp_path: Path) -> None:
    output = tmp_path / "pilot.jsonl"
    records = run_benchmark(
        split="dev", repeats=1, output=output, timeout=30, task_ids={"tier1-01"}
    )

    assert len(records) == len(ARMS)
    assert {record["arm"] for record in records} == set(ARMS)
    assert all(record["task_list_hash"] for record in records)
    assert all(record["response_source"] == "oracle-replay" for record in records)
    assert output.read_text(encoding="utf-8").count("\n") == len(records)


def test_benchmark_uses_recorded_patch_responses(tmp_path: Path) -> None:
    responses = tmp_path / "responses.jsonl"
    responses.write_text(
        json.dumps({"task_id": "tier1-01", "arm": "single", "patch": None}) + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "results.jsonl"
    records = run_benchmark(
        split="dev",
        repeats=1,
        output=output,
        responses=responses,
        timeout=30,
        task_ids={"tier1-01"},
    )
    first = next(
        record
        for record in records
        if record["task_id"] == "tier1-01" and record["arm"] == "single"
    )
    assert first["response_source"] == "recording"
    assert first["winner_declared"] is False


def test_benchmark_never_declares_winner_when_full_suite_fails(tmp_path: Path) -> None:
    patch = tmp_path / "wrong.patch"
    patch.write_text(
        "--- a/bug.py\n"
        "+++ b/bug.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def answer() -> int:\n"
        "-    return 0\n"
        "+    return 3\n",
        encoding="utf-8",
    )
    responses = tmp_path / "responses.jsonl"
    responses.write_text(
        json.dumps({"task_id": "tier1-01", "arm": "single", "patch": str(patch)}) + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "results.jsonl"
    records = run_benchmark(
        split="dev",
        repeats=1,
        output=output,
        responses=responses,
        timeout=30,
        task_ids={"tier1-01"},
    )
    first = next(
        record
        for record in records
        if record["task_id"] == "tier1-01" and record["arm"] == "single"
    )
    assert first["selected_passed"] is True
    assert first["full_suite_runs"] == 1
    assert first["winner_declared"] is False
