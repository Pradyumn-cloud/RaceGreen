# RaceGreen benchmark protocol

The Tier 1 suite is synthetic and legally redistributable. It has 24 repair
tasks and 6 controls, with no network or third-party checkout required.
Repair metadata covers easy, medium, and hard difficulty plus flaky,
unrelated-failure, and broad-change cases. Controls include unsolvable,
weak-visible, already-passing, and tempting-test-edit cases.

## Layout and safety

* `benchmarks/manifest.schema.json` is the versioned manifest contract.
* `benchmarks/manifests/*.yaml` contains one manifest per task. Each file uses
  JSON syntax, which is valid YAML, so the dependency-free validator parses it
  with the standard library.
* `benchmarks/tier1/tasks.jsonl` is generated compatibility output.
* `benchmarks/tier1/generate.py` regenerates manifests, oracle patches, and
  deterministic synthetic checkouts.
* `.bench/` is the only location for clones, worktrees, caches, and results.
  It is ignored by Git and must never be committed.

`scripts/validate_task.py` accepts either one YAML manifest or the manifest
directory. For synthetic tasks it invokes the materializer, creates a local Git
base, and applies that task's committed oracle patch in an isolated clone.
Commands are JSON arrays passed directly to `subprocess.run`; shell strings are
rejected. The validator does not install dependencies.

## Required measurements

Validation records JSONL events containing environment fingerprints and command
outcomes. It performs pristine checks, oracle-fix validation, three baseline
runs for flakiness, and held-out discrimination. Controls explicitly set
`control: true`; they are baseline measurements and are never counted as
discriminating repair tasks.

The flaky task uses Python's standard-library `secrets` source and is
nondeterministic across runs. Its `known_flaky` manifest flag is reported
alongside observed flakiness. For unrelated pre-existing failures, validation
uses F2P/P2P semantics: a repair is accepted when the target tests pass and
the oracle preserves the identical baseline failure signature (F2P), or when
the complete suite passes (P2P). It never treats a changed failure as
acceptable.

`scripts/analyze_results.py` summarizes fake or real JSONL data. Selected tests
are advisory; a future winner still requires full-suite confirmation.

The protocol strata are: easy, medium, hard, flaky, unrelated pre-existing
failure, and broad-change. Tasks 1–18 are the development split and the
remainder are held-out test tasks; every manifest contains the frozen task-list
hash. Tier 2 external repository tasks remain pending until their licenses,
commits, setup locks, and reproducible fixtures are reviewed. Analysis includes
deterministic bootstrap confidence intervals, per-stratum rates, and a
solved-by-time curve. Provider cost and regret against the fastest resolved
task are not yet reported by the offline replay harness.

## Development-split replay result

The final development-split replay is stored at `.bench/pipeline-dev-final.jsonl`
and can be reproduced with:

```bash
uv run python scripts/analyze_results.py .bench/pipeline-dev-final.jsonl
```

The run contains 192 records: 24 development tasks across eight arms, including
144 repair records and 48 controls. Every arm resolved all 18 repair tasks per
arm (`resolved_rate = 1.0`, bootstrap 95% CI `[1.0, 1.0]`) with
`false_winner_rate = 0.0`. Across the full run, the harness recorded 145
full-suite runs avoided and a median duration of 10.98 seconds.

| Arm | Median seconds | Full-suite runs avoided |
| --- | ---: | ---: |
| `single` | 10.70 | 6 |
| `full_suite_only` | 10.70 | 5 |
| `no_prefilter` | 10.73 | 29 |
| `sequential_retry` | 10.88 | 29 |
| `no_selection` | 11.63 | 29 |
| `racegreen` | 11.67 | 27 |
| `parallel_wait_all` | 11.95 | 10 |
| `wait_all_no_cancel` | 12.04 | 10 |

This result validates the pipeline, event, confirmation, and arbitration
protocols against checked-in oracle and control patches. It is an
`offline-replay`/`oracle-replay` run, not a live-model quality measurement.
`oracle_pass_count` and `held_out_discrimination_count` are `null` because the
pipeline records do not contain validator-only fields. The replay also does not
measure provider cost, and `no_selection`, `full_suite_only`, and
`wait_all_no_cancel` remain partially overlapping protocol arms. Docker
verification was unit-tested but was not exercised against a live Docker daemon
in this benchmark. The development split is not the held-out test split; do not
present these numbers as a generalization claim.


## Phase 8 benchmark sequence

Run a pilot on the development split before hardening. The development split
contains 18 repair tasks plus six controls, so the harness emits 24 tasks:

```bash
uv run python scripts/benchmark_budget.py --arms 8 --tasks 24 --repeats 1
```

Use the estimate to reserve provider capacity before launching the pilot. The
final run must record the provider, exact model ID, run date, protocol version,
task-list hash, response-recording policy, latency, failures, and cost. Freeze
the code and protocol before the final test-split run; any code change after
the freeze invalidates its results.

The executable harness is `scripts/benchmark.py`. It runs the eight protocol
arms in interleaved task/repeat order and writes one JSONL result per
arm/task/repeat:

```bash
uv run python scripts/benchmark.py --split dev --repeats 1 \
  --output .bench/pilot.jsonl
uv run python scripts/analyze_results.py .bench/pilot.jsonl
```

By default the harness replays the checked-in oracle and control patches. This
checks the benchmark pipeline, not LLM quality. A provider adapter may export
recorded responses as JSONL with `task_id`, `arm`, and `patch` fields, then
replay them with `--responses`. Keep response recordings private when they
contain repository or provider data.

Validator results also record a dependency-free differential mutation check:
the oracle implementation is changed to return a wrong value and the visible
test must kill that mutant. `mutation_status` reports `killed`, `survived`, or
`not-run`; this is a lightweight strength signal, not a substitute for a full
mutation-testing tool.
