# RaceGreen Phase 0 benchmark protocol

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

The protocol strata are: easy (10 tasks), medium (11), hard (3), flaky (1),
unrelated pre-existing failure (1), and broad-change (2). Tasks 1–18 are the
development split and the remainder are held-out test tasks; every manifest
contains the frozen task-list hash. Tier 2 external repository tasks remain
pending until their licenses, commits, setup locks, and reproducible fixtures
are reviewed. Analysis includes deterministic bootstrap confidence intervals,
per-stratum rates, cost per resolved task, regret against the fastest resolved
task, and a solved-by-time curve.

## Phase 8 benchmark sequence

Run a pilot on the development split before hardening:

```bash
uv run python scripts/benchmark_budget.py --arms 8 --tasks 18 --repeats 1
```

Use the estimate to reserve provider capacity before launching the pilot. The
final run must record the provider, exact model ID, run date, protocol version,
task-list hash, response-recording policy, latency, failures, and cost. Freeze
the code and protocol before the final test-split run; any code change after
the freeze invalidates its results.

Validator results also record a dependency-free differential mutation check:
the oracle implementation is changed to return a wrong value and the visible
test must kill that mutant. `mutation_status` reports `killed`, `survived`, or
`not-run`; this is a lightweight strength signal, not a substitute for a full
mutation-testing tool.
