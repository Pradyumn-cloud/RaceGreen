# RaceGreen

RaceGreen runs bounded coding-agent attempts in separate Git worktrees,
verifies candidate patches, confirms winners with the configured full test
suite, and produces reviewable evidence.

The project uses Python 3.12, `uv`, Ruff, mypy, pytest, asyncio, SQLite, and
Typer. Agents use an OpenAI-compatible provider adapter. RaceGreen is intended
for trusted repositories and controlled experiments; it is not a security
sandbox.

## Requirements

- Python 3.12
- Git
- `uv`
- WSL2/Linux is recommended for the repository, database, and artifacts

Install locked dependencies:

```bash
uv sync --locked
```

## Offline demo

The demo requires no API key or network:

```bash
uv run racegreen demo
uv run racegreen doctor
```

It creates a sample SQLite trace and escaped, self-contained HTML report.
The demo validates local persistence and reporting only; it is not evidence of
agent quality.

## Provider setup

Credentials are read from the environment and are never written to traces or
artifacts:

```bash
export RACEGREEN_PROVIDER_URL="https://api.openai.com"
export RACEGREEN_MODEL="your-model-id"
read -r -s -p "API key: " RACEGREEN_API_KEY
export RACEGREEN_API_KEY
printf '\n'
```

For a local loopback provider:

```bash
export RACEGREEN_PROVIDER_URL="http://127.0.0.1:11434"
export RACEGREEN_MODEL="qwen2.5-coder:7b"
```

Use one local model request at a time on systems with 16 GB RAM.

## Run an agent

Run against a clean, trusted repository:

```bash
uv run racegreen agent-run /path/to/repository "Fix the failing test"
```

For multiple bounded attempts:

```bash
uv run racegreen race /path/to/repository "Fix the failing test" \
  --agents 2
```

The source repository is not modified automatically. Candidate patches and
events are stored under the RaceGreen run artifacts directory.

## Inspect runs

RaceGreen creates the SQLite database automatically:

```bash
uv run racegreen runs
uv run racegreen run-show RUN_ID
uv run racegreen events list RUN_ID
uv run racegreen db verify
uv run racegreen report RUN_ID --output report.html
```

Keep the database and artifacts on the WSL/Linux filesystem, not `/mnt/c`.

## Benchmark

The synthetic Tier 1 benchmark can validate the benchmark pipeline without a
provider:

```bash
uv run python scripts/benchmark.py \
  --split dev --repeats 1 --output .bench/pilot-replay.jsonl
uv run python scripts/analyze_results.py .bench/pilot-replay.jsonl
```

Oracle replay is an infrastructure check, not an LLM-quality benchmark.
Provider-backed results must record the exact model, provider, date, task-list
hash, responses, failures, latency, and cost. See
[docs/benchmark.md](docs/benchmark.md).

## Security

Git worktrees provide version-control isolation, not a security sandbox.
Agent-generated commands run with the current user's permissions and may use
the network. Run untrusted repositories only inside a restricted container or
VM with separate credentials and network controls. Read
[SECURITY.md](SECURITY.md) before running agents.

## Documentation

- [Agent operations](docs/agent.md)
- [Benchmark protocol](docs/benchmark.md)
- [Release checklist](RELEASE_CHECKLIST.md)
- [Traceability matrix](docs/traceability.md)
- [Security policy](SECURITY.md)
- [Changelog](CHANGELOG.md)
