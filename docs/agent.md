# RaceGreen complete usage guide

This guide covers the supported commands, configuration, run inspection,
cleanup, replay, and benchmark workflow.

## 1. Prerequisites

RaceGreen is tested with:

- Python 3.12
- Git
- `uv`
- WSL2/Linux for the repository, SQLite database, and artifacts

From WSL, place the project on the Linux filesystem when possible:

```bash
mkdir -p "$HOME/src"
cd "$HOME/src"
```

Install the locked environment from the project directory:

```bash
uv sync --locked
```

Confirm the command is available:

```bash
uv run racegreen --help
uv run racegreen-replay --help
```

## 2. Security requirements

Git worktrees provide Git isolation only. They are not an OS security
sandbox. Agent-generated commands run with your user permissions and may use
the network.

For untrusted repositories, run RaceGreen inside a restricted container or VM
with separate credentials and controlled filesystem/network access. Keep the
RaceGreen database and artifacts on the WSL/Linux filesystem, not `/mnt/c`.
Read [SECURITY.md](../SECURITY.md) before running agents.

Use a clean source repository. RaceGreen refuses dirty source trees by
default so that the captured base commit and resulting patch remain clear.

## 3. Provider configuration

RaceGreen uses an OpenAI-compatible chat-completions provider.

The easiest setup is:

```bash
uv run racegreen quickstart --provider ollama --model qwen2.5-coder:7b
uv run racegreen doctor
uv run racegreen provider-test
```

Use `racegreen setup` for an interactive wizard. Setup writes provider
metadata, never credentials. Existing `RACEGREEN_*` environment variables
remain supported and override file values. Local providers do not require API
keys. RaceGreen probes the endpoint you configure; it does not guess WSL
gateway addresses, start services, or download models.

The user configuration path is platform-specific (`%APPDATA%\racegreen\config.json`
on Windows, `$XDG_CONFIG_HOME/racegreen/config.json` or
`~/.config/racegreen/config.json` elsewhere). Override it with
`RACEGREEN_CONFIG`. `racegreen init --repo .` writes repository-local settings
to `.racegreen/config.json`; local values override global values, and files
containing credential-looking keys are rejected. `racegreen doctor --fix`
creates safe directories and a missing config template without overwriting an
existing configuration.

### Hosted provider

Set these variables in the same shell used to run RaceGreen:

```bash
export RACEGREEN_PROVIDER_URL="https://api.openai.com"
export RACEGREEN_MODEL="exact-model-id"
read -r -s -p "API key: " RACEGREEN_API_KEY
export RACEGREEN_API_KEY
printf '\n'
```

Optional custom API-key variable:

```bash
export RACEGREEN_API_KEY_ENV="MY_PROVIDER_KEY"
export MY_PROVIDER_KEY="your-key"
```

Remote provider URLs must use HTTPS. Use the exact model identifier accepted
by the provider.

### xAI or another compatible provider

```bash
export RACEGREEN_PROVIDER_URL="https://api.x.ai/v1"
export RACEGREEN_MODEL="exact-supported-model-id"
read -r -s -p "API key: " RACEGREEN_API_KEY
export RACEGREEN_API_KEY
printf '\n'
```

Do not use a key from a different provider. Never put keys in source files,
JSON config, prompts, event files, SQLite, or reports.

### Local Ollama provider

For an RTX 4050 and 16 GB RAM, begin with one 7B/8B coder model and
concurrency one:

```bash
ollama pull qwen2.5-coder:7b
ollama serve
```

In another shell:

```bash
export RACEGREEN_PROVIDER_URL="http://127.0.0.1:11434"
export RACEGREEN_MODEL="qwen2.5-coder:7b"
```

Loopback HTTP is allowed for local providers. No hosted API key is required.

For Ollama across a Windows/WSL boundary, configure the reachable Ollama base
URL explicitly and follow the Ollama `OLLAMA_HOST` and firewall requirements
for that installation.

## 4. Local dashboard

Inspect persisted runs in the loopback-only dashboard:

```bash
uv run racegreen dashboard --open
```

The dashboard is read-only and provides run search, state filters, summary
metrics, candidate lifecycle and patch inspection, winner evidence, agent
status, and a live event timeline. The timeline uses server-sent events,
supports event/agent/candidate filters and follow mode, and falls back to
polling if the browser does not support `EventSource`.

Treat agent and test output as untrusted text. The dashboard escapes rendered
values, serves only local assets under a restrictive Content Security Policy,
rejects cross-origin requests, and never executes commands from the browser.
The `NEW RUN` dialog only prepares a copyable CLI command.

Candidate patch content is available only when the candidate's recorded patch
path resolves inside the run directory under the dashboard's artifacts root.
Symlink escapes and oversized patches are rejected. Pass the same artifacts
root used when creating the run:

```bash
uv run racegreen dashboard --open \
  --database "$HOME/.racegreen/racegreen.db" \
  --artifacts-root "$HOME/.racegreen/runs"
```

The dashboard shows whether a winner has recorded full-suite confirmation
evidence. It does not re-verify the decision; use the event trace and reports
for the authoritative verification record. The server binds to loopback by
default and permits only loopback `Host` values and same-port loopback `Origin`
values.

## 5. Check the installation

Run the diagnostic command:

```bash
uv run racegreen doctor
```

It reports Python, Git, provider configuration, provider URL configuration,
and home-directory writability. It does not contact the provider.

`doctor` may report `"ok": false` when no provider is configured; that is
expected for the offline demo. It exits unsuccessfully only when Python 3.12,
Git, or the home directory requirement fails.

Create a safe, credential-free configuration template:

```bash
uv run racegreen init
```

Use a custom path if needed:

```bash
uv run racegreen init --config "$HOME/.config/racegreen/config.json"
```

`init` refuses to overwrite an existing file. Credentials still belong in
protected environment injection, not this file.

## 5. Offline demo

The demo requires no provider, API key, or network:

```bash
uv run racegreen demo
```

It creates:

```text
.racegreen-demo/demo.db
.racegreen-demo/report.html
```

Use a WSL-native output directory:

```bash
uv run racegreen demo \
  --artifacts-root "$HOME/.racegreen/demo"
```

The demo validates SQLite trace storage and report rendering. It does not
measure agent quality or provider behavior.

## 6. Run one coding agent

Run against a clean, trusted repository:

```bash
uv run racegreen agent-run /path/to/repository \
  "Inspect the repository, fix the failing test, run the tests, and submit a candidate only after verification."
```

Useful limits:

```bash
uv run racegreen agent-run /path/to/repository "Fix the failing test" \
  --timeout-seconds 300 \
  --max-tool-calls 100 \
  --artifacts-root "$HOME/.racegreen/runs" \
  --database "$HOME/.racegreen/racegreen.db"
```

The command:

1. Captures the source base commit.
2. Creates a RaceGreen-owned worktree.
3. Runs one bounded provider conversation.
4. Stores bounded events and candidate patch snapshots.
5. Imports the events into SQLite.
6. Prints JSON containing `run_id`, `agent_id`, `status`, event path, and
   artifact path.

The source repository is not automatically changed.

## 7. Run a multi-agent race

```bash
uv run racegreen race /path/to/repository \
  "Fix the failing test and verify the complete test suite." \
  --agents 2 \
  --max-concurrency 2 \
  --verification-command "pytest -q"
```

Additional controls:

```bash
uv run racegreen race /path/to/repository "Fix the failing test" \
  --agents 3 \
  --max-concurrency 2 \
  --timeout-seconds 300 \
  --max-tokens-per-run 20000 \
  --max-cost-usd-per-run 2.00 \
  --max-full-suite-runs 10 \
  --max-changed-files 20 \
  --max-changed-lines 500 \
  --hedge-delay 1.5 \
  --agent-model qwen2.5-coder:7b \
  --agent-model qwen2.5-coder:14b \
  --winner-policy first_confirmed \
  --artifacts-root "$HOME/.racegreen/runs" \
  --database "$HOME/.racegreen/racegreen.db"
```

`--agents` must not exceed `--max-concurrency`. Local providers default to one
concurrent agent. `--agent-model` is repeatable; agents receive models in
rotation. `--hedge-delay` staggers agent starts without changing verification.
Press `Ctrl+C` to request cancellation; the process tree is terminated and late
candidates are rejected.

A successful candidate always requires configured full-suite confirmation;
selected tests, agent claims, or model review cannot declare a winner by
themselves. Verification rejects missing JUnit output, new failures relative to
the base-commit baseline, clone/checkout/apply failures, and configured quality
gates. If the base commit already has unrelated failures, a candidate is
confirmed only when the configured target-test command passes and the
full-suite failure set is unchanged or smaller; without a separate target-test
command, baseline failures remain fatal. If no candidate is confirmed the run
ends `EXHAUSTED`.

Winner policies:

- `first_confirmed` — declare the earliest fully confirmed candidate.
- `best_of_confirmed` — choose the smallest confirmed patch; earlier
  confirmation is the tie-break. Patch size is disclosed and is not correctness
  evidence.
- `consensus` — require a quorum of confirmed candidates with the same patch
  hash. Use `--consensus-quorum` to override the default quorum.

The race automatically exports a reviewable winner patch and evidence unless
`--require-approval` is set. Integration is a separate, re-checked operation:

```bash
uv run racegreen winner show --run-id RUN_ID
uv run racegreen winner export --run-id RUN_ID --output winner.patch
uv run racegreen winner branch --run-id RUN_ID --branch racegreen/winner
uv run racegreen winner apply --run-id RUN_ID
```

`winner apply` refuses a target whose state changed since the captured base
commit and defaults to a reviewable patch or branch rather than modifying the
user's current branch.

An external agent command can replace the built-in provider loop:

```bash
uv run racegreen race . "Fix the failing test" \
  --external-agent "my-agent --non-interactive" \
  --verification-command "pytest -q"
```

The command runs once in each worktree without a shell and submits the worktree
snapshot through the normal candidate intake path. It receives the task in
`RACEGREEN_TASK` plus `RACEGREEN_RUN_ID`, `RACEGREEN_AGENT_ID`, and
`RACEGREEN_WORKTREE`. It is not a security sandbox.

Verification can run in Docker:

```bash
uv run racegreen race . "Fix the failing test" \
  --verification-command "python -m pytest -q" \
  --sandbox docker \
  --sandbox-image my-test-image:latest
```

Only the disposable verification checkout is mounted, at `/work`. The default
network is `none`, the root filesystem is read-only, and `/tmp` is writable.
The image must contain the test runtime and dependencies.

## 8. Database and run inspection

The SQLite database is created automatically. The default is:

```text
~/.racegreen/racegreen.db
```

Prefer an explicit WSL-native path:

```bash
DB="$HOME/.racegreen/racegreen.db"
```

List recorded runs:

```bash
uv run racegreen runs --database "$DB"
```

Show one run, including projections and event count:

```bash
uv run racegreen run-show RUN_ID --database "$DB"
```

Verify SQLite integrity, contiguous event sequences, and winner invariants:

```bash
uv run racegreen db verify --database "$DB"
```

Rebuild run, agent, and candidate projections by replaying the persisted event
trace. Existing winner rows are preserved:

```bash
uv run racegreen db rebuild --database "$DB"
```

Opening an older database upgrades its schema and rebuilds projections
automatically. Use `db rebuild` after manual event ingestion or if a projection
looks stale. The event rows remain the authoritative trace.

Import an existing JSONL event file:

```bash
uv run racegreen db ingest /path/to/events.jsonl --database "$DB"
```

The database is the runtime source of truth. JSONL is an event export or
compatibility input, not a second authoritative store.

## 9. Events and reports

List events:

```bash
uv run racegreen events list RUN_ID --database "$DB"
```

Read only events after a sequence number:

```bash
uv run racegreen events list RUN_ID \
  --after 20 \
  --limit 100 \
  --database "$DB"
```

Export a derived JSONL timeline:

```bash
uv run racegreen events export RUN_ID \
  --output "$HOME/.racegreen/RUN_ID.jsonl" \
  --database "$DB"
```

Export to standard output:

```bash
uv run racegreen events export RUN_ID --database "$DB"
```

Create a self-contained escaped HTML report:

```bash
uv run racegreen report RUN_ID \
  --output "$HOME/.racegreen/RUN_ID-report.html" \
  --database "$DB"
```

The report treats agent and test output as untrusted text. Do not serve
artifacts as executable web content.

## 10. Replay events

Replay an event file without contacting a provider:

```bash
uv run racegreen-replay show /path/to/events.jsonl
```

Output is sanitized for terminal control and escape sequences.

## 11. Cleanup

List RaceGreen-owned run directories:

```bash
uv run racegreen clean \
  --artifacts-root "$HOME/.racegreen/runs"
```

Remove one named run only after reviewing the list:

```bash
uv run racegreen clean \
  --run-id RUN_ID \
  --artifacts-root "$HOME/.racegreen/runs" \
  --yes
```

Cleanup only removes direct child directories containing RaceGreen's ownership
marker. It does not remove source repositories or arbitrary directories.

## 12. Synthetic benchmark

Estimate provider cost and wall time:

```bash
uv run python scripts/benchmark_budget.py \
  --arms 8 \
  --tasks 24 \
  --repeats 1 \
  --calls-per-run 10 \
  --tokens-per-run 20000 \
  --cost-per-million-tokens 5 \
  --concurrency 1 \
  --seconds-per-run 300
```

Run the real pipeline harness:

```bash
uv run python scripts/benchmark.py \
  --split dev \
  --engine pipeline \
  --repeats 1 \
  --output "$HOME/.racegreen/dev-pipeline.jsonl"
```

Run one task while debugging the harness:

```bash
uv run python scripts/benchmark.py \
  --split dev \
  --engine pipeline \
  --task tier1-01 \
  --repeats 1 \
  --output "$HOME/.racegreen/tier1-01.jsonl"
```

The fixture engine is still available for fast protocol checks, but it is not
evidence for RaceGreen's verification or arbitration behavior:

```bash
uv run python scripts/benchmark.py \
  --split dev \
  --engine fixture \
  --output "$HOME/.racegreen/dev-fixture.jsonl"
```

Analyze results:

```bash
uv run python scripts/analyze_results.py \
  "$HOME/.racegreen/dev-pipeline.jsonl"
```

Analysis reports metrics per arm, bootstrap confidence intervals, false-winner
counts, full-suite runs avoided, and a single-agent comparison. Held-out tests
are applied to a fresh repository only after a winner is confirmed, so a
visible-suite pass is not counted as a resolved task.

Replay recorded candidate responses:

```bash
uv run python scripts/benchmark.py \
  --split dev \
  --responses /path/to/responses.jsonl \
  --output "$HOME/.racegreen/provider-results.jsonl"
```

Each response record must contain `task_id`, `arm`, and `patch`. Keep response
recordings private if they contain repository or provider data.

Oracle replay validates benchmark plumbing only. It must not be presented as
an LLM performance result. Provider-backed pilot and final benchmark runs
must record the exact model, provider, date, task-list hash, response policy,
latency, failures, and cost.

## 13. Development checks

Run the full supported suite in WSL:

```bash
uv sync --locked
uv run ruff check . --select E4,E7,E9,F,I
uv run mypy src/racegreen/storage src/racegreen/report.py src/racegreen/cli.py
uv run pytest
uvx --from pip-audit pip-audit
```

The repository CI also runs the demo and Gitleaks. Do not commit generated
`.bench` results, local databases, credentials, or provider response
recordings.

## 14. Common failures

### `RACEGREEN_MODEL must be set`

Set `RACEGREEN_MODEL` in the current shell. `doctor` can confirm whether the
variable is present.

### `Incorrect API key provided`

Check that the key belongs to the configured provider and that the endpoint
and exact model ID match. Do not reuse a key from another provider.

### Dirty repository refusal

Commit or stash your source changes, or run RaceGreen against a clean clone.
Do not bypass this protection by deleting unrelated user work.

### Database or WAL problems

Move the database and active artifacts from `/mnt/c` into the WSL/Linux
filesystem, such as `$HOME/.racegreen`.

### Provider timeout or rate limit

Reduce agent concurrency, lower the run budget, verify provider limits, and
record the failure. Do not claim a benchmark result from incomplete runs.
