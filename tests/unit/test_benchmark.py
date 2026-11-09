from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from benchmarks.tier1.generate import build_tasks

ROOT = Path(__file__).parents[2]


def test_tier1_has_expected_task_and_control_counts() -> None:
    tasks = build_tasks()
    assert len(tasks) == 30
    assert sum(bool(task["control"]) for task in tasks) == 6
    assert sum(not bool(task["control"]) for task in tasks) == 24
    assert all(isinstance(item, str) for task in tasks for item in task["commands"]["full"])
    manifests = list((ROOT / "benchmarks" / "manifests").glob("*.yaml"))
    assert len(manifests) == 30
    assert {manifest.stem for manifest in manifests} == {str(task["task_id"]) for task in tasks}


def test_analysis_handles_fake_jsonl(tmp_path: Path) -> None:
    results = tmp_path / "fake.jsonl"
    results.write_text(
        json.dumps(
            {
                "event": "task_result",
                "oracle_passed": True,
                "flaky": False,
                "held_out_discriminated": True,
                "duration_seconds": 2,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, "scripts/analyze_results.py", str(results)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    summary = json.loads(completed.stdout)
    assert summary["task_count"] == 1
    assert summary["held_out_discrimination_count"] == 1
    assert summary["resolved_count"] == 0
    assert summary["false_winner_rate"] is None
    assert summary["full_suite_runs_avoided"] == 0


def test_analysis_compares_arms_and_single_agent(tmp_path: Path) -> None:
    results = tmp_path / "arms.jsonl"
    records = [
        {
            "event": "task_result",
            "arm": "single",
            "control": False,
            "winner_declared": True,
            "held_out_passed": True,
            "time_to_resolve_seconds": 10,
            "duration_seconds": 10,
        },
        {
            "event": "task_result",
            "arm": "racegreen",
            "control": False,
            "winner_declared": True,
            "held_out_passed": True,
            "time_to_resolve_seconds": 8,
            "duration_seconds": 8,
        },
    ]
    results.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, "scripts/analyze_results.py", str(results)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    summary = json.loads(completed.stdout)

    assert summary["by_arm"]["single"]["resolved_rate"] == 1.0
    assert summary["by_arm"]["racegreen"]["resolved_rate"] == 1.0
    assert summary["by_arm"]["racegreen"]["bootstrap_resolved_rate_95ci"] == [1.0, 1.0]
    delta = summary["comparison"]["single_vs_racegreen"]
    assert delta["resolved_rate_delta"] == 0.0
    assert delta["median_duration_seconds_delta"] == -2.0


def test_analysis_bootstrap_uses_all_repair_tasks(tmp_path: Path) -> None:
    results = tmp_path / "arms.jsonl"
    records = [
        {
            "event": "task_result",
            "arm": "racegreen",
            "control": False,
            "winner_declared": True,
            "held_out_passed": True,
            "duration_seconds": 1,
        },
        {
            "event": "task_result",
            "arm": "racegreen",
            "control": False,
            "winner_declared": True,
            "held_out_passed": False,
            "duration_seconds": 1,
        },
    ]
    results.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, "scripts/analyze_results.py", str(results)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    summary = json.loads(completed.stdout)

    assert summary["by_arm"]["racegreen"]["resolved_rate"] == 0.5
    assert summary["by_arm"]["racegreen"]["bootstrap_resolved_rate_95ci"] == [0.0, 1.0]


def test_flaky_and_unrelated_contracts_are_explicit() -> None:
    tasks = build_tasks()
    flaky = next(task for task in tasks if task["kind"] == "flaky")
    unrelated = next(task for task in tasks if task["kind"] == "unrelated")
    assert flaky["known_flaky"] is True
    assert unrelated["known_flaky"] is False
    assert unrelated["kind"] == "unrelated"


def test_yaml_is_source_for_jsonl_and_agent_view_has_no_leaks(tmp_path: Path) -> None:
    from benchmarks.tier1.generate import materialize_task

    task = next(task for task in build_tasks() if task["kind"] == "flaky")
    task["_include_held_out"] = False
    materialize_task(task, tmp_path / "fixture")
    assert not (tmp_path / "fixture" / "tests" / "test_held_out.py").exists()
    assert not (tmp_path / "fixture" / "oracle").exists()
    assert (
        subprocess.run(
            ["git", "-C", str(tmp_path / "fixture"), "rev-list", "--count", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        == "1"
    )
    yaml_task = json.loads((ROOT / "benchmarks/manifests/tier1-01.yaml").read_text())
    jsonl_task = json.loads((ROOT / "benchmarks/tier1/tasks.jsonl").read_text().splitlines()[0])
    assert yaml_task == jsonl_task


def test_control_decoys_are_declared_and_committed() -> None:
    controls = [task for task in build_tasks() if task["control"]]
    weak = next(task for task in controls if task["kind"] == "weak-visible")
    tempting = next(task for task in controls if task["kind"] == "tempting-test-edit")
    assert len(weak["decoy_patches"]) == 1
    assert len(tempting["decoy_patches"]) == 1
    for task in (weak, tempting):
        patch = ROOT / "benchmarks" / "tier1" / task["decoy_patches"][0]
        assert patch.is_file()
        assert patch.read_text(encoding="utf-8").startswith("--- ")
