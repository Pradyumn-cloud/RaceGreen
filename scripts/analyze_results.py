"""Summarize benchmark JSONL output without external dependencies.

The top-level metrics retain the original combined view. ``by_arm`` and
``arms`` are the comparison view: every arm is evaluated on the same tasks and
repeats, and confidence intervals are bootstrap intervals over task outcomes.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
from pathlib import Path
from typing import Any


def _bootstrap_ci(outcomes: list[float]) -> list[float] | None:
    if not outcomes:
        return None
    rng = random.Random(0)
    samples: list[float] = []
    for _ in range(2000):
        sample = [rng.choice(outcomes) for _ in outcomes]
        samples.append(sum(sample) / len(sample))
    samples.sort()
    return [
        round(samples[int(len(samples) * 0.025)], 6),
        round(samples[min(len(samples) - 1, int(len(samples) * 0.975))], 6),
    ]


def _metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    tasks = [record for record in records if record.get("event") == "task_result"]
    repair_tasks = [record for record in tasks if not record.get("control", False)]
    winners = [record for record in repair_tasks if record.get("winner_declared") is True]
    resolved = [record for record in winners if record.get("held_out_passed") is True]
    outcomes = [
        1.0 if record.get("held_out_passed") is True else 0.0
        for record in repair_tasks
    ]
    durations = [
        float(record["duration_seconds"])
        for record in tasks
        if isinstance(record.get("duration_seconds"), (int, float))
    ]
    resolved_times = [
        float(record["time_to_resolve_seconds"])
        for record in resolved
        if isinstance(record.get("time_to_resolve_seconds"), (int, float))
    ]
    strata: dict[str, dict[str, Any]] = {}
    for record in repair_tasks:
        key = str(record.get("stratum", record.get("difficulty", "unknown")))
        bucket = strata.setdefault(key, {"task_count": 0, "resolved_count": 0})
        bucket["task_count"] += 1
        bucket["resolved_count"] += int(record.get("held_out_passed") is True)
    curve = []
    for threshold in (30, 60, 120, 300, 600):
        curve.append(
            {
                "seconds": threshold,
                "resolved_count": sum(t <= threshold for t in resolved_times),
            }
        )
    false_winners = [record for record in winners if record.get("held_out_passed") is not True]
    oracle_values = [
        record.get("oracle_passed") is True
        for record in tasks
        if "oracle_passed" in record
    ]
    discrimination_values = [
        record.get("held_out_discriminated") is True
        for record in repair_tasks
        if "held_out_discriminated" in record
    ]
    return {
        "task_count": len(tasks),
        "repair_task_count": len(repair_tasks),
        "oracle_pass_count": sum(oracle_values) if oracle_values else None,
        "flaky_count": sum(
            record.get("flaky") is True or record.get("known_flaky") is True
            for record in tasks
        ),
        "held_out_discrimination_count": (
            sum(discrimination_values) if discrimination_values else None
        ),
        "winner_count": len(winners),
        "resolved_count": len(resolved),
        "resolved_rate": len(resolved) / len(repair_tasks) if repair_tasks else None,
        "winner_rate": len(winners) / len(repair_tasks) if repair_tasks else None,
        "false_winner_count": len(false_winners),
        "false_winner_rate": len(false_winners) / len(winners) if winners else None,
        "full_suite_runs_avoided": sum(
            int(record.get("full_suite_runs_avoided", 0))
            for record in tasks
            if isinstance(record.get("full_suite_runs_avoided", 0), int)
        ),
        "error_count": sum("error" in record for record in tasks),
        "median_duration_seconds": statistics.median(durations) if durations else None,
        "bootstrap_resolved_rate_95ci": _bootstrap_ci(outcomes),
        "per_stratum": strata,
        "solved_by_time_curve": curve,
    }


def analyze(records: list[dict[str, Any]]) -> dict[str, Any]:
    combined = _metrics(records)
    arms = sorted(
        {str(record.get("arm", "unknown")) for record in records if record.get("event") == "task_result"}
    )
    by_arm = {
        arm: _metrics(
            [record for record in records if str(record.get("arm", "unknown")) == arm]
        )
        for arm in arms
    }
    single = by_arm.get("single")
    race = by_arm.get("racegreen")
    comparison: dict[str, Any] = {}
    if single is not None and race is not None:
        comparison = {
            "single_vs_racegreen": {
                "resolved_rate_delta": _delta(
                    race.get("resolved_rate"), single.get("resolved_rate")
                ),
                "false_winner_rate_delta": _delta(
                    race.get("false_winner_rate"), single.get("false_winner_rate")
                ),
                "median_duration_seconds_delta": _delta(
                    race.get("median_duration_seconds"), single.get("median_duration_seconds")
                ),
                "full_suite_runs_avoided_delta": _delta(
                    race.get("full_suite_runs_avoided"),
                    single.get("full_suite_runs_avoided"),
                ),
            }
        }
    combined["arms"] = by_arm
    combined["by_arm"] = by_arm
    combined["comparison"] = comparison
    return combined


def _delta(left: Any, right: Any) -> float | None:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return float(left) - float(right)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    args = parser.parse_args()
    # Accept PowerShell's UTF-8 BOM while writing our own output as plain UTF-8.
    with args.results.open(encoding="utf-8-sig") as stream:
        records = [json.loads(line) for line in stream if line.strip()]
    print(json.dumps(analyze(records), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
