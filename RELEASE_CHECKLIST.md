# RaceGreen v0.1.0 release checklist

## Before hardening

- [ ] Run the pilot benchmark on the development split.
- [ ] Record model ID, provider, date, latency, failures, tokens, and cost.
- [ ] Decide whether the result supports a final benchmark.

## Hardening

- [ ] Complete the traceability matrix in [docs/traceability.md](docs/traceability.md).
- [ ] Run the full suite repeatedly to expose flaky races.
- [ ] Run branch coverage for arbiter, winner slot, and path authority.
- [ ] Run property tests for FSM and path-authority invariants.
- [ ] Test persistence interruption and `PRAGMA integrity_check`.
- [ ] Run secret scanning, dependency auditing, Ruff, mypy, and pytest.
- [ ] Confirm agent command network limitations are documented.

## Freeze

- [ ] Commit the protocol version.
- [ ] Commit the task-list hash.
- [ ] Pin the model ID and record the run date.
- [ ] Freeze code before the final test-split benchmark.
- [ ] Reject any post-freeze code changes as invalidating results.

## Final benchmark

- [ ] Run all arms and ablations in interleaved order.
- [ ] Include failed, timed-out, rate-limited, and unsolved runs.
- [ ] Publish verified time-to-patch and solved-by-time results.
- [ ] Publish resolved rate, cost, false-winner rate, regret, full-suite runs
      avoided, selection misses, and cancellation latency.

## Release

- [ ] Verify `uv sync --locked` on a fresh CI runner.
- [ ] Verify `racegreen demo` without credentials or network.
- [ ] Review the final diff for secrets and runtime artifacts.
- [ ] Update `CHANGELOG.md` and README limitations.
- [ ] Create and verify the `v0.1.0` tag.
