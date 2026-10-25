from racegreen.scheduler.rate_limit import SharedRateLimiter


def test_shared_rate_limiter_acquire():
    r = SharedRateLimiter(max_per_second=10, burst=2)
    assert r.acquire(timeout=1.0)
    assert r.acquire(timeout=1.0)
    r.record_rate_limited(retry_after=0.01)
    # should still be able to acquire eventually
    assert r.acquire(timeout=1.0)
