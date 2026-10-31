from scripts.benchmark_budget import estimate


def test_budget_estimate_scales_all_dimensions():
    result = estimate(
        arms=8,
        tasks=30,
        repeats=3,
        calls_per_run=10,
        tokens_per_run=20_000,
        cost_per_million_tokens=5,
        concurrency=4,
        seconds_per_run=300,
    )
    assert result["runs"] == 720
    assert result["provider_calls"] == 7200
    assert result["tokens"] == 14_400_000
    assert result["estimated_wall_seconds"] == 54_000
