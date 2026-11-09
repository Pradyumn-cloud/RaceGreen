# Changelog

## Unreleased

### Added

- End-to-end race pipeline: scheduler, candidate intake, verification worker
  pool, arbiter, loser cancellation, and winner export.
- Full-suite confirmation with JUnit parsing, baseline comparison, evidence
  validation, and an explicit `EXHAUSTED` result when no candidate is confirmed.
- Winner policies: `first_confirmed`, `best_of_confirmed`, and `consensus`.
- Quality gates for patch size, changed file count, and changed line count.
- Hedged agent starts, per-agent model selection, and an external-agent command
  adapter.
- Optional Docker verification sandbox (`--sandbox docker --sandbox-image ...`).
- Repository-local configuration (`.racegreen/config.json`) and cross-platform
  user configuration paths.
- `racegreen quickstart`, `doctor --fix`, `--require-approval`, and winner
  `show`/`export`/`branch`/`apply` commands.
- Race `--dry-run` readiness checks and `racegreen resume` for interrupted runs
  that still match their recorded base commit.
- Dashboard run comparison for persisted state, winner evidence, candidate
  counts, duration, and base commit.
- Real-pipeline benchmark engine, per-arm analysis with bootstrap confidence
  intervals, and a single-agent comparison.
- CI matrix, coverage, dependency audit, release workflow, Dependabot, and a
  composite GitHub Action.

### Changed

- Python support is `>=3.12`; pytest is allowed below 10.
- Local providers default to one concurrent agent.
- Verification rejects missing JUnit output, new baseline failures, clone or
  checkout failures, and patch application mismatches. When the base commit
  already has unrelated failures, confirmation also requires a passing
  configured target-test command and an unchanged or smaller baseline failure
  set.
- Candidate snapshots and Git fixtures use LF-stable checkout to avoid Windows
  line-ending false positives.
- Candidate IDs are made unique across agents and sanitized before use as
  filenames, so an agent cannot overwrite another snapshot or escape the
  candidate directory.
- Provider calls are bounded by the agent's remaining wall-time budget.
- Pre-filters reject edits to test files and test configuration, not only
  top-level pytest configuration.

### Security

- Verification runs with a minimal environment allowlist and bounded command
  output; timed-out or canceled commands terminate their full process tree.
- External agents use a bounded environment allowlist and process-tree
  cancellation; they are explicitly not a security sandbox. Provider
  credentials are not passed unless named in
  `RACEGREEN_EXTERNAL_PASSTHROUGH`.
- Candidate pre-filters reject symlink and submodule patch entries as well as
  test-file and test-configuration edits.
- Worktree cleanup keeps its ownership manifest when removal fails, allows a
  later cleanup to retry, and refuses to delete branches not owned by the run.
- Docker verification mounts only the disposable checkout and defaults to
  `--network none`.
