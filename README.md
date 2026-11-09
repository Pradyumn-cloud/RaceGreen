# RaceGreen

RaceGreen runs multiple coding agents on the same task in isolated Git
worktrees. It verifies candidate patches, requires a configured full-suite
confirmation before declaring a winner, cancels unnecessary work, and produces
a reviewable patch or branch.

The project prioritizes correctness, isolation, reproducible evidence, bounded
provider cost, and safe cleanup. Worktrees are version-control isolation, not a
security sandbox.

## Install

From a release:

```bash
pipx install racegreen
```

From a checkout:

```bash
uv sync --locked
uv run racegreen --help
# or: pipx install .
```

Python 3.12 or newer is required. Git is required; ripgrep is optional because
RaceGreen falls back to a bounded Python search.

## Quickstart

The non-interactive quickstart writes a safe provider configuration, creates
local directories, and runs the prerequisite checks:

```bash
racegreen quickstart --provider ollama --model qwen2.5-coder:7b
racegreen doctor
racegreen provider-test
```

For hosted providers, pass `--model` and set the credential environment
variable named in your config (normally `RACEGREEN_API_KEY`). Credentials are
never written to configuration, events, SQLite, reports, or artifacts.

Repository-local configuration is supported at `.racegreen/config.json`:

```bash
racegreen init --repo .
```

## Run a verified race

```bash
racegreen race /path/to/repository "Fix the failing parser test" \
  --agents 2 \
  --verification-command "pytest -q" \
  --winner-policy first_confirmed
```

The race:

1. captures an immutable base commit and creates one worktree per agent;
2. snapshots each submitted candidate as a hashed patch;
3. applies the snapshot in a disposable clone and runs a base-commit baseline;
4. runs the configured full suite and parses JUnit output;
5. rejects new failures, missing JUnit, clone/checkout/apply failures, and
   quality-gate violations;
6. lets a deterministic winner policy choose among confirmed candidates;
7. cancels remaining work after a durable winner is recorded.

A run ends `COMPLETED` only when a winner exists. Submissions that never receive
full-suite confirmation end `EXHAUSTED`; they are not reported as successes.

Validate configuration and repository state without starting agents:

```bash
racegreen race /path/to/repository "task" --verification-command "pytest -q" --dry-run
```

`--dry-run` prints a JSON readiness report and exits non-zero when the repository
is dirty, the verification executable is missing, the provider is not configured,
or a Docker sandbox was requested without Docker. It does not create worktrees,
run agents, or write run state.

### Resume an interrupted attempt

```bash
racegreen resume <run-id> --dry-run
racegreen resume <run-id>
```

`resume` starts a new attempt with the same task, base commit, verification
settings, budgets, and winner policy. It never reuses candidate snapshots or
worktrees from the old attempt. It refuses if the repository HEAD moved away
from the recorded base commit or if the old run already has a winner. Use
`winner export` instead when a confirmed winner already exists.

### Winner policies

- `first_confirmed` — the earliest fully confirmed candidate.
- `best_of_confirmed` — the smallest confirmed patch, with earlier confirmation
  as a deterministic tie-break. Patch size is disclosed as a tie-break, never
  as correctness evidence.
- `consensus` — requires a quorum of confirmed candidates with the same patch
  hash. The default quorum is 2 for a two-agent race and a strict majority for
  larger races; use `--consensus-quorum` to override.

### Review and integration

Winner application is separate from winner declaration:

```bash
racegreen winner show --run-id <run-id>
racegreen winner export --run-id <run-id> --output winner.patch
racegreen winner branch --run-id <run-id> --branch racegreen/winner
racegreen winner apply --run-id <run-id>       # re-checks the target state
```

Use `--require-approval` to skip automatic winner export and require an explicit
operator action. `winner apply` refuses to modify a target that has moved away
from the captured base commit.

## Agent and verification controls

```bash
racegreen race . "task" \
  --agents 2 --max-concurrency 2 \
  --hedge-delay 1.5 \
  --agent-model qwen2.5-coder:7b --agent-model qwen2.5-coder:14b \
  --max-changed-files 20 --max-changed-lines 500 \
  --max-full-suite-runs 10 \
  --timeout-seconds 600
```

`--agent-model` is repeatable. Local providers default to one concurrent agent
to avoid overloading a single GPU. `--hedge-delay` staggers agent starts.

An external agent command can be used instead of the built-in provider loop:

```bash
racegreen race . "task" --external-agent "my-agent --non-interactive"
```

The command runs once in each worktree without a shell, using a bounded
environment allowlist and receives the task in `RACEGREEN_TASK`. See
[SECURITY.md](SECURITY.md).

### Docker verification sandbox

```bash
racegreen race . "task" \
  --verification-command "python -m pytest -q" \
  --sandbox docker \
  --sandbox-image my-test-image:latest
```

The image must contain the test runtime and dependencies. The disposable
verification checkout is mounted at `/work`; the default network is `none`, and
the root filesystem is read-only with a writable `/tmp`. This sandbox covers
verification commands only. The Docker daemon, image, and mounted worktree are
still part of the trust boundary.

## Inspect runs

```bash
racegreen runs
racegreen run-show --run-id <run-id>
racegreen report --run-id <run-id> --output report.html
racegreen dashboard --open
```

The dashboard binds to loopback by default. It is read-only: the browser cannot
start runs, apply patches, or execute repository commands. Candidate patches
are served only from the configured artifacts root, and patch responses are
size-capped. The run detail view includes a `COMPARE` tab for side-by-side
persisted state, winner, evidence, candidate counts, duration, and base commit
for two runs. Comparison values are projections of stored events, not a
re-verification.

## Offline demo

The persistence and report flow can be exercised without an API key or network:

```bash
uv run racegreen demo
```

The demo is not evidence of agent quality.

## Benchmarks

The benchmark CLI defaults to the real scheduler/verifier/arbiter pipeline:

```bash
uv run python scripts/benchmark.py --split dev --engine pipeline \
  --output .bench/pipeline-dev.jsonl
uv run python scripts/analyze_results.py .bench/pipeline-dev.jsonl
```

The fixture engine remains available for fast protocol checks:

```bash
uv run python scripts/benchmark.py --split dev --engine fixture --output .bench/fixture-dev.jsonl
```

Held-out tests are applied to a fresh repository only after a winner is
confirmed, so a visible-suite pass cannot be mistaken for a false winner.
Analysis reports per-arm resolved rates, false-winner rates, full-suite runs
avoided, bootstrap confidence intervals, and a single-agent comparison.

## Security limitation

Agent commands run with the current user's permissions. Git worktrees isolate
version-control state but are not an OS sandbox, and agent command network
access is not disabled by RaceGreen alone. Use a container or VM for untrusted
repositories. See [SECURITY.md](SECURITY.md).

## Development

```bash
uv sync --locked
uv run pytest -q
uv run ruff check . --select E4,E7,E9,F,I
uv run mypy src/racegreen
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md).
