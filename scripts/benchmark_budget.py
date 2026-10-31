"""Estimate benchmark size, provider calls, tokens, cost, and wall time."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def estimate(
    *,
    arms: int,
    tasks: int,
    repeats: int,
    calls_per_run: int,
    tokens_per_run: int,
    cost_per_million_tokens: float,
    concurrency: int,
    seconds_per_run: float,
) -> dict[str, float | int]:
    runs = arms * tasks * repeats
    tokens = runs * tokens_per_run
    return {
        "runs": runs,
        "provider_calls": runs * calls_per_run,
        "tokens": tokens,
        "estimated_cost": tokens / 1_000_000 * cost_per_million_tokens,
        "estimated_wall_seconds": runs * seconds_per_run / max(concurrency, 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arms", type=int, default=8)
    parser.add_argument("--tasks", type=int, default=30)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--calls-per-run", type=int, default=10)
    parser.add_argument("--tokens-per-run", type=int, default=20_000)
    parser.add_argument("--cost-per-million-tokens", type=float, default=5.0)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--seconds-per-run", type=float, default=300.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = estimate(
        arms=args.arms,
        tasks=args.tasks,
        repeats=args.repeats,
        calls_per_run=args.calls_per_run,
        tokens_per_run=args.tokens_per_run,
        cost_per_million_tokens=args.cost_per_million_tokens,
        concurrency=args.concurrency,
        seconds_per_run=args.seconds_per_run,
    )
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
