"""Summarize validator JSONL output without external dependencies."""

from __future__ import annotations

import argparse
import json
import random
import statistics
from pathlib import Path
from typing import Any


def analyze(records: list[dict[str, Any]]) -> dict[str, Any]:
    tasks = [record for record in records if record.get("event") == "task_result"]
    repair_tasks = [record for record in tasks if not record.get("control", False)]
    resolved_times = [
        float(r["time_to_resolve_seconds"])
        for r in repair_tasks
        if r.get("resolved") is True and isinstance(r.get("time_to_resolve_seconds"), (int, float))
    ]
    costs = [float(r["cost"]) for r in repair_tasks if isinstance(r.get("cost"), (int, float))]
    rng = random.Random(0)
    bootstrap: list[float] = []
    outcomes = [1.0 if r.get("resolved") is True else 0.0 for r in repair_tasks]
    for _ in range(1000):
        sample = [rng.choice(outcomes) for _ in outcomes] if outcomes else []
        bootstrap.append(sum(sample) / len(sample) if sample else 0.0)
    bootstrap.sort()
    ci = (
        [
            bootstrap[int(len(bootstrap) * 0.025)],
            bootstrap[min(len(bootstrap) - 1, int(len(bootstrap) * 0.975))],
        ]
        if bootstrap
        else None
    )
    strata: dict[str, dict[str, Any]] = {}
    for record in repair_tasks:
        key = str(record.get("stratum", record.get("difficulty", "unknown")))
        bucket = strata.setdefault(key, {"task_count": 0, "resolved_count": 0})
        bucket["task_count"] += 1
        bucket["resolved_count"] += int(record.get("resolved") is True)
    curve = []
    for threshold in (30, 60, 120, 300, 600):
        curve.append(
            {"seconds": threshold, "resolved_count": sum(t <= threshold for t in resolved_times)}
        )
    durations = [
        float(record["duration_seconds"])
        for record in tasks
        if isinstance(record.get("duration_seconds"), (int, float))
    ]
    winners = [
        record
        for record in repair_tasks
        if record.get("winner_declared") is True
    ]
    resolved = [record for record in winners if record.get("held_out_passed") is True]
    return {
        "task_count": len(tasks),
        "oracle_pass_count": sum(record.get("oracle_passed") is True for record in tasks),
        "flaky_count": sum(record.get("flaky") is True for record in tasks),
        "held_out_discrimination_count": sum(
            record.get("held_out_discriminated") is True for record in repair_tasks
        ),
        "resolved_count": len(resolved),
        "resolved_rate": len(resolved) / len(repair_tasks) if repair_tasks else None,
        "false_winner_count": sum(
            record.get("winner_declared") is True and record.get("held_out_passed") is False
            for record in winners
        ),
        "false_winner_rate": (
            sum(
                record.get("winner_declared") is True and record.get("held_out_passed") is False
                for record in winners
            )
            / len(winners)
            if winners
            else None
        ),
        "full_suite_runs_avoided": sum(
            int(record.get("full_suite_runs_avoided", 0))
            for record in tasks
            if isinstance(record.get("full_suite_runs_avoided", 0), int)
        ),
        "error_count": sum("error" in record for record in tasks),
        "median_duration_seconds": statistics.median(durations) if durations else None,
        "bootstrap_resolved_rate_95ci": ci,
        "per_stratum": strata,
        "cost_per_resolved": sum(costs) / len(resolved_times) if resolved_times else None,
        "regret_seconds": (
            sum(resolved_times) - min(resolved_times) * len(resolved_times)
            if resolved_times
            else None
        ),
        "solved_by_time_curve": curve,
    }


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
