# RaceGreen

RaceGreen runs one or more coding agents against the same task in isolated Git
worktrees. Every submitted patch is frozen, hashed, applied in a disposable
clone, and checked against the repository's full test suite. A candidate becomes
the winner only after the configured full-suite confirmation succeeds. RaceGreen
then stops unnecessary work and leaves a reviewable patch or branch for a human
to approve.

RaceGreen is built for teams and developers who want the speed and diversity of
parallel coding agents without giving up verification, isolation, or an
auditable decision trail.

## Why RaceGreen

Coding agents are useful, but a plausible-looking patch is not evidence of
correctness. Running several agents in parallel creates three problems:

- **False confidence.** An agent can claim success, pass a narrow test, or delete
  the behavior that was supposed to be preserved.
- **Operational risk.** Parallel agents can overwrite work, leave processes
  running, or touch a repository that has moved underneath them.
- **Unbounded cost.** More attempts mean more tokens, more time, and more test
  runs unless the race has explicit limits.

RaceGreen addresses those problems with a verification-first design:

- **Correctness before speed.** A candidate cannot win on agent claims, selected
  tests, or model review. The configured full suite must confirm it.
- **Isolation by default.** Each agent works in its own Git worktree. Candidate
  patches are immutable snapshots with content hashes.
- **Reproducible evidence.** Base commits, patches, JUnit results, event traces,
  and winner evidence are persisted and inspectable.
- **Bounded execution.** Wall time, concurrency, token, cost, patch size, and
  full-suite budgets are explicit.
- **Human review in the integration path.** Winner declaration and winner
  application are separate operations. The default output is a patch or review
  branch, not an automatic commit to your current branch.
- **Safe failure.** A run with no confirmed candidate ends `EXHAUSTED`; it is
  never reported as a success.

## Where to use RaceGreen

RaceGreen is a good fit when the repository has a meaningful test suite and the
task has a checkable outcome:

- Fixing a failing test or a reproducible bug.
- Comparing several models, prompts, or agent configurations on the same task.
- Evaluating a coding-agent harness before adopting it in a workflow.
- Running a bounded repair race in CI or on a developer workstation.
- Producing a reviewable candidate patch with recorded verification evidence.

RaceGreen is not a replacement for code review, a guarantee that a patch is
correct beyond the tests it was given, or an operating-system sandbox for
untrusted code. Use a container or virtual machine when the repository, agent, or
test commands are not trusted. See [SECURITY.md](SECURITY.md).

## Feature overview

| Capability | Command | What it provides |
| --- | --- | --- |
| Verified multi-agent race | `racegreen race <repo> "<task>"` | Bounded agents, isolated worktrees, full-suite confirmation, deterministic winner policy |
| Single bounded agent | `racegreen agent-run <repo> "<task>"` | One provider conversation in a private worktree with event and candidate persistence |
| Readiness check | `racegreen race <repo> "<task>" --dry-run` | Validates repository state, provider configuration, verification command, and sandbox settings without starting agents |
| Resume | `racegreen resume <run-id>` | Starts a new attempt from the recorded base commit without reusing old worktrees or candidates |
| Winner inspection | `racegreen winner show <run-id>` | Shows the durable winner and its full-suite evidence |
| Winner export | `racegreen winner export <run-id> --output winner.patch` | Exports the verified patch without modifying the target repository |
| Review branch | `racegreen winner branch <run-id> --repository <repo>` | Creates a `racegreen/<run-id>` branch containing the verified patch |
| Explicit apply | `racegreen winner apply <run-id> --repository <repo>` | Applies the winner only after a fresh target-state check and confirmation |
| Run history | `racegreen runs` | Lists persisted runs from SQLite |
| Run detail | `racegreen run-show <run-id>` | Shows the persisted run snapshot and projections |
| HTML report | `racegreen report <run-id> --output report.html` | Writes a self-contained, escaped report from SQLite |
| Dashboard | `racegreen dashboard --open` | Opens a loopback-only, read-only run viewer with live event streaming |
| Event inspection | `racegreen events list <run-id>` | Lists persisted events for a run |
| Event export | `racegreen events export <run-id> --output events.jsonl` | Exports a derived JSONL timeline |
| Database verification | `racegreen db verify` | Checks SQLite integrity, event sequence, and winner invariants |
| Database rebuild | `racegreen db rebuild` | Replays persisted events into run, agent, and candidate projections |
| Event ingestion | `racegreen db ingest <events.jsonl>` | Imports an existing event trace into the authoritative database |
| Provider setup | `racegreen setup` | Configures a provider without storing credentials |
| Guided setup | `racegreen quickstart` | Writes configuration, runs local checks, and prints next commands |
| Repository configuration | `racegreen init --repo <repo>` | Creates `.racegreen/config.json` without embedding credentials |
| Diagnostics | `racegreen doctor` | Checks Python, Git, provider configuration, and writable directories |
| Provider probe | `racegreen provider-test` | Tests provider reachability without sending a model completion |
| Offline demo | `racegreen demo` | Exercises SQLite trace storage and report rendering without credentials or network |
| Cleanup | `racegreen clean` | Lists or removes only explicitly-owned RaceGreen run directories |
| Benchmark harness | `python scripts/benchmark.py` | Runs the eight-arm benchmark protocol through the real pipeline or fixture engine |

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

Requirements:

- Python 3.12 or newer.
- Git.
- `ripgrep` is optional. RaceGreen falls back to a bounded Python search.

On Windows, use PowerShell or a WSL2 shell. For the best SQLite and filesystem
performance under WSL2, keep the repository, database, and run artifacts on the
Linux filesystem rather than `/mnt/c`.

## Quick start

### Local Ollama provider

```bash
racegreen quickstart --provider ollama --model qwen2.5-coder:7b
racegreen doctor
racegreen provider-test
```

The quickstart writes provider metadata, creates local directories, and runs
the prerequisite checks. It does not download models or start Ollama.

If you prefer explicit environment variables:

```bash
export RACEGREEN_PROVIDER_URL="http://127.0.0.1:11434"
export RACEGREEN_MODEL="qwen2.5-coder:7b"
```

PowerShell equivalent:

```powershell
$env:RACEGREEN_PROVIDER_URL = "http://127.0.0.1:11434"
$env:RACEGREEN_MODEL = "qwen2.5-coder:7b"
```

### Hosted OpenAI-compatible provider

```bash
export RACEGREEN_PROVIDER_URL="https://api.example.com"
export RACEGREEN_MODEL="exact-model-id"
export RACEGREEN_API_KEY="your-key"
```

PowerShell equivalent:

```powershell
$env:RACEGREEN_PROVIDER_URL = "https://api.example.com"
$env:RACEGREEN_MODEL = "exact-model-id"
$env:RACEGREEN_API_KEY = "your-key"
```

Remote provider URLs must use HTTPS. Use the exact model identifier accepted by
the provider. Credentials belong in environment injection or an OS credential
store; they are never written to configuration files, events, SQLite, reports,
or artifacts.

Repository-local configuration is supported at `.racegreen/config.json`:

```bash
racegreen init --repo .
```

Local values override global values. Files containing credential-looking keys
are rejected.

## Run a verified race

```bash
racegreen race /path/to/repository "Fix the failing parser test" \
  --agents 2 \
  --max-concurrency 2 \
  --verification-command "pytest -q" \
  --winner-policy first_confirmed \
  --require-approval
```

The race:

1. Captures an immutable base commit and creates one worktree per agent.
2. Runs bounded provider conversations or an external agent command.
3. Freezes each submitted candidate as a hashed patch snapshot.
4. Applies the snapshot in a disposable clone and runs a base-commit baseline.
5. Runs the configured full suite and parses JUnit output.
6. Rejects new failures, missing JUnit output, no-test runs, clone/checkout/apply
   failures, test-file edits, and configured quality-gate violations.
7. Lets a deterministic winner policy choose among confirmed candidates.
8. Cancels remaining work after a durable winner is recorded.
9. Leaves a reviewable patch or branch for operator approval.

A run ends `COMPLETED` only when a winner exists. Submissions that never receive
full-suite confirmation end `EXHAUSTED`; they are not reported as successes.

### Dry-run first

Validate configuration and repository state without starting agents:

```bash
racegreen race /path/to/repository "Fix the failing test" \
  --verification-command "pytest -q" \
  --dry-run
```

The dry run prints a JSON readiness report and exits non-zero when the repository
is dirty, the verification executable is missing, the provider is not
configured, or a Docker sandbox was requested without Docker. It does not create
worktrees, run agents, or write run state.

### Defaults and controls

`race` uses conservative defaults:

- With a local provider, the default is one agent and one concurrent agent.
- With a hosted provider, the default is two agents and two concurrent agents.
- `--agents` must not exceed `--max-concurrency`.
- The default winner policy is `first_confirmed`.
- The default full-suite command is `pytest -q`.
- The default full-suite budget is 10 runs.
- The default maximum patch size is 5 MiB.
- `--verification-command` is split into an argument array; no shell is used.

Important options:

| Option | Purpose |
| --- | --- |
| `--agents N` | Number of competing agents. |
| `--max-concurrency N` | Maximum agents running at the same time. |
| `--timeout-seconds N` | Per-agent wall-time limit. |
| `--max-tokens-per-run N` | Run-level token budget. |
| `--max-cost-usd-per-run N` | Run-level cost budget. |
| `--max-full-suite-runs N` | Maximum full-suite runs for the race. |
| `--max-changed-files N` | Reject candidates that touch too many files. |
| `--max-changed-lines N` | Reject candidates with too many changed lines. |
| `--max-patch-bytes N` | Reject oversized candidate patches. |
| `--hedge-delay SECONDS` | Stagger agent starts. |
| `--agent-model MODEL` | Select a model; repeat to rotate models across agents. |
| `--external-agent COMMAND` | Run an external agent command once per worktree. |
| `--sandbox docker` | Run verification in a Docker sandbox. |
| `--sandbox-image IMAGE` | Image containing the test runtime and dependencies. |
| `--require-approval` | Do not auto-export a winner; require an explicit operator step. |
| `--allow-dirty` | Allow a source repository with uncommitted changes. |
| `--no-progress` | Disable the animated status spinner. |
| `--database PATH` | Override the SQLite database path. |
| `--artifacts-root PATH` | Override the run artifacts root. |

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

## Verification model

RaceGreen does not trust agent claims. Verification is staged and evidence-based:

1. **Base-commit baseline.** The configured full suite runs at the captured base
   commit. JUnit failures are recorded so the verifier can distinguish new
   failures from pre-existing ones.
2. **Candidate pre-filter.** Candidate patches are rejected when they edit test
   files or test configuration, contain symlink or submodule entries, exceed the
   patch-size limit, or violate changed-file and changed-line gates.
3. **Disposable checkout.** The candidate patch is applied in a fresh clone, not
   in a moving agent worktree.
4. **Full-suite confirmation.** The configured full suite runs with JUnit output.
   Missing JUnit output, no collected tests, clone/checkout/apply failures, and
   new failures are rejections.
5. **Baseline-failure handling.** If the base commit already has unrelated
   failures, a candidate must pass the configured target-test command and must
   not increase the full-suite failure set. Without a separate target-test
   command, baseline failures remain fatal.
6. **Bounded retries.** Infrastructure and flaky failures are classified and
   retried within explicit limits. Retries are recorded.
7. **Winner evidence.** Only a candidate with complete full-suite confirmation
   can transition to `WON`. The arbiter validates the evidence before recording a
   durable winner.

Selected-test staging exists in the verification layer, but selected tests are
advisory. They cannot replace full-suite confirmation.

### Winner policies

- `first_confirmed` - the earliest fully confirmed candidate.
- `best_of_confirmed` - the smallest confirmed patch, with earlier confirmation
  as a deterministic tie-break. Patch size is disclosed as a tie-break, never as
  correctness evidence.
- `consensus` - requires a quorum of confirmed candidates with the same patch
  hash. The default quorum is 2 for a two-agent race and a strict majority for
  larger races; use `--consensus-quorum` to override.

### Run and candidate states

RaceGreen keeps run state and candidate state separate:

```text
Run:        PREPARING -> RUNNING -> COMPLETED
                                  -> EXHAUSTED
                                  -> CANCELLED
                                  -> FAILED
                                  -> UNVERIFIABLE

Candidate:  RECEIVED -> PREFILTERING -> PREFILTER_PASSED
          -> SELECTED_TESTS -> CONFIRMING -> WON
                                         -> CONFIRMED_BUT_NOT_SELECTED
                                         -> REJECTED
                                         -> CANCELLED
                                         -> INFRA_FAILED
```

Invalid transitions are rejected. Candidate transitions are not encoded as run
transitions.

## Review and integrate a winner

Winner declaration and winner application are separate operations:

```bash
racegreen winner show <run-id>
racegreen winner export <run-id> --output winner.patch
racegreen winner branch <run-id> --repository /path/to/repository
racegreen winner apply <run-id> --repository /path/to/repository
```

- `winner show` requires complete full-suite evidence.
- `winner export` writes the verified patch without touching the target.
- `winner branch` creates the branch `racegreen/<run-id>` in the target
  repository.
- `winner apply` re-checks the target HEAD against the captured base commit and
  asks for confirmation unless `--yes` is supplied.
- `--require-approval` prevents automatic winner export during the race.

The default integration path is reviewable output, not an automatic commit to
your current branch. If the target has changed since the race started, apply
refuses with a stale-target result. Use the exported patch or branch for manual
review.

## Inspect runs and evidence

```bash
racegreen runs
racegreen run-show <run-id>
racegreen report <run-id> --output report.html
racegreen dashboard --open
```

The dashboard binds to loopback by default. It is read-only: the browser cannot
start runs, apply patches, or execute repository commands. Candidate patches are
served only from the configured artifacts root, and patch responses are
size-capped. The run detail view includes a `COMPARE` tab for side-by-side
persisted state, winner, evidence, candidate counts, duration, and base commit
for two runs. Comparison values are projections of stored events, not a
re-verification.

### Events and database

```bash
racegreen events list <run-id>
racegreen events list <run-id> --after 20 --limit 100
racegreen events export <run-id> --output events.jsonl
racegreen db verify
racegreen db rebuild
racegreen db ingest events.jsonl
```

The SQLite database is the runtime source of truth. JSONL is an event export or
compatibility input, not a second authoritative store. Opening an older database
upgrades its schema and rebuilds projections automatically. Use `db rebuild`
after manual event ingestion or if a projection looks stale.

### Reports and replay

```bash
racegreen report <run-id> --output report.html
racegreen-replay show events.jsonl
```

Reports and replay treat agent and test output as untrusted text. Do not serve
artifacts as executable web content.

## Agent controls and external agents

The built-in agent loop uses an OpenAI-compatible chat-completions provider. It
supports bounded tool calls for reading, searching, editing, running commands,
and submitting candidates.

An external agent command can replace the built-in provider loop:

```bash
racegreen race . "Fix the failing test" \
  --external-agent "my-agent --non-interactive" \
  --verification-command "pytest -q"
```

The command runs once in each worktree without a shell, using a bounded
environment allowlist. It receives:

- `RACEGREEN_TASK`
- `RACEGREEN_RUN_ID`
- `RACEGREEN_AGENT_ID`
- `RACEGREEN_WORKTREE`

A successful external command still submits through the normal candidate intake
path and must pass the same full-suite verification and arbitration. The adapter
is not a security sandbox. See [SECURITY.md](SECURITY.md).

## Docker verification sandbox

```bash
racegreen race . "Fix the failing test" \
  --verification-command "python -m pytest -q" \
  --sandbox docker \
  --sandbox-image my-test-image:latest
```

The image must contain the test runtime and dependencies. The disposable
verification checkout is mounted at `/work`; the default network is `none`; the
root filesystem is read-only with a writable `/tmp`.

This sandbox covers verification commands only. The Docker daemon, image, and
mounted worktree remain part of the trust boundary. Use a prepared image instead
of enabling network access for untrusted tests.

## Architecture

```text
CLI and configuration
        |
        v
Workspace manager
  - captures the base commit
  - creates one Git worktree per agent
  - records ownership for safe cleanup
        |
        v
Scheduler
  - bounded concurrency and hedging
  - per-agent and run budgets
  - cancellation and late-event rejection
        |
        v
Agent runner
  - OpenAI-compatible provider or external command
  - bounded tool loop
  - candidate submission
        |
        v
Candidate intake
  - freezes the worktree as a patch
  - computes a content hash
  - runs the pre-filter
        |
        v
Verification pipeline
  - base-commit baseline
  - disposable checkout
  - full-suite confirmation with JUnit
  - bounded retries and classification
        |
        v
Arbiter
  - validates complete evidence
  - applies the deterministic winner policy
  - records the winner durably
        |
        v
Integration and inspection
  - show, export, branch, apply
  - SQLite projections
  - event timeline, reports, dashboard
```

### Component responsibilities

- **Workspace manager:** Resolves and validates paths, captures the starting
  commit and working-tree state, creates RaceGreen-owned worktrees, and removes
  only resources it can positively identify as its own.
- **Scheduler:** Runs agents with bounded concurrency, applies hedging and
  budgets, owns process cancellation, and rejects late candidates after a winner
  is declared.
- **Agent runner:** Owns one provider conversation or external command. It
  records every response and tool event, enforces wall-time and tool-call limits,
  and submits only through candidate intake.
- **Candidate intake:** Converts a worktree into an immutable patch snapshot with
  a content hash. Verification never runs against a moving worktree.
- **Verification pipeline:** Runs baseline and confirmation commands in isolated
  checkouts, parses JUnit evidence, classifies failures, and applies retry and
  budget policy.
- **Arbiter:** Validates that a candidate has complete full-suite evidence before
  it can become `WON`, applies the configured winner policy, and persists the
  decision atomically.
- **Integration manager:** Provides separate show, export, branch, and apply
  operations with patch-hash and target-state checks.
- **Storage and dashboard:** Persist events and projections in SQLite, expose
  loopback-only inspection, and render untrusted text safely.

### Design invariants

- Every run records an immutable base commit and configuration fingerprint.
- Every candidate is an immutable patch snapshot with a content hash.
- A candidate cannot enter `WON` based on pre-filters, selected tests, agent
  claims, or model review.
- Only successful configured full-suite confirmation may transition a candidate
  to `WON`.
- A test-selection miss or unsupported mapping must broaden verification or fall
  back to the full suite.
- Flaky and infrastructure failures are distinguishable from deterministic test
  failures. Retries are bounded and recorded.
- Candidate verification is isolated from concurrent edits.
- Cancellation is idempotent. A canceled agent cannot submit a late candidate
  that changes the winner.
- Tie-breaking is documented, deterministic, and never presented as correctness
  evidence.
- Event persistence failure stops the run safely or marks it unverifiable.

## Security and operational limits

RaceGreen treats agent output, model output, repository contents, test output,
and task descriptions as untrusted input.

- Git worktrees provide version-control isolation, not an operating-system
  security sandbox. Agent commands run with the current user's permissions.
- Built-in agent commands and external agents use a minimal environment
  allowlist. Provider credentials are not inherited unless explicitly named.
- Commands are launched with argument arrays, never shell-concatenated strings.
- Timeouts and cancellation terminate the full process tree and record the
  outcome.
- Candidate paths are resolved and validated. Path traversal and symlink escapes
  are rejected.
- The dashboard binds to loopback, rejects non-loopback hosts and cross-origin
  requests, escapes untrusted text, and never executes commands from the browser.
- Cleanup removes only directories with RaceGreen's ownership marker. It does not
  remove source repositories or arbitrary paths.
- Provider credentials must remain in environment injection or an OS credential
  store. Do not put them in configuration files, prompts, logs, SQLite, reports,
  or artifacts.
- For untrusted repositories, run RaceGreen inside a restricted container or
  virtual machine with separate credentials and controlled filesystem and
  network access.

See [SECURITY.md](SECURITY.md) for the full security model.

## Offline demo

The persistence and report flow can be exercised without an API key or network:

```bash
racegreen demo
```

The demo creates:

```text
.racegreen-demo/demo.db
.racegreen-demo/report.html
```

The demo is not evidence of agent quality.

## Benchmarks

The benchmark CLI defaults to the real scheduler, verifier, and arbiter pipeline:

```bash
uv run python scripts/benchmark.py --split dev --engine pipeline \
  --output .bench/pipeline-dev.jsonl
uv run python scripts/analyze_results.py .bench/pipeline-dev.jsonl
```

The fixture engine remains available for fast protocol checks:

```bash
uv run python scripts/benchmark.py --split dev --engine fixture \
  --output .bench/fixture-dev.jsonl
```

The recorded development-split replay contains 192 records: 24 development tasks
across eight arms, including 144 repair records and 48 controls. Every arm
resolved all 18 repair tasks per arm (`resolved_rate = 1.0`) with
`false_winner_rate = 0.0`. The harness recorded 145 full-suite runs avoided and a
median duration of 10.98 seconds.

This is an offline/oracle replay that validates the pipeline, event,
confirmation, and arbitration protocols. It is not a live-model quality
measurement. The development split is not the held-out test split, provider cost
is not measured by the replay, and the Docker sandbox was unit-tested but not
exercised against a live Docker daemon in the recorded benchmark. See
[docs/benchmark.md](docs/benchmark.md) for the full protocol and limitations.

## Project status

RaceGreen is alpha software. The core race, verification, arbitration, and
inspection paths are implemented and tested. Interfaces, persisted schemas, and
benchmark protocols may still evolve; changes should remain backward compatible
where practical.

Known follow-up work is tracked in [upcoming.md](upcoming.md).

## Documentation

- [docs/agent.md](docs/agent.md) - complete command, configuration, and
  troubleshooting guide.
- [docs/benchmark.md](docs/benchmark.md) - benchmark protocol, measurements, and
  limitations.
- [docs/traceability.md](docs/traceability.md) - requirement-to-check audit
  index.
- [SECURITY.md](SECURITY.md) - security model and deployment guidance.
- [CONTRIBUTING.md](CONTRIBUTING.md) - contribution workflow.
- [AGENTS.md](AGENTS.md) - engineering requirements for this repository.
- [CHANGELOG.md](CHANGELOG.md) - release notes.

## Development

```bash
uv sync --locked
uv run pytest -q
uv run ruff check . --select E4,E7,E9,F,I
uv run mypy src/racegreen
```

The repository CI also runs the offline demo, coverage, secret scanning, and
dependency audit. Do not commit generated `.bench` results, local databases,
credentials, or provider response recordings.

## License

MIT. See [LICENSE](LICENSE).
