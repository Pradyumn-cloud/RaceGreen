# Agent operations

The single-agent command runs one provider conversation in a RaceGreen-owned
Git worktree. The provider can be hosted or local, but the initial adapter
uses the OpenAI-compatible chat-completions protocol.

## Configuration

Credentials are read only from the environment:

```bash
export RACEGREEN_API_KEY="your-key"
export RACEGREEN_MODEL="your-model-id"
export RACEGREEN_PROVIDER_URL="https://api.openai.com"
```

`RACEGREEN_PROVIDER_URL` defaults to `https://api.openai.com`. Remote
providers must use HTTPS. Local HTTP endpoints are allowed only on loopback,
for example `http://127.0.0.1:11434`.

The API key is never written to event files or run artifacts. The model,
endpoint, and a configuration fingerprint are recorded so runs can be
reproduced without persisting credentials.

## Running one agent

Run from a clean repository because workspace creation refuses dirty source
trees by default:

```bash
uv run racegreen agent-run /path/to/repository "Fix the failing test"
```

Optional controls:

```bash
uv run racegreen agent-run /path/to/repository "Fix the failing test" \
  --timeout-seconds 300 \
  --max-tool-calls 100 \
  --artifacts-root /home/user/.racegreen/runs
```

The command creates a private worktree and persists JSONL events and candidate
patches under the run artifact directory. A submitted candidate is a patch
snapshot. RaceGreen does not apply changes to the source branch automatically.

Replay persisted events without contacting the provider:

```bash
uv run racegreen-replay show /path/to/events.jsonl
```

## Trace inspection and reports

Agent and race commands import their persisted events into the local SQLite
trace database. Keep that database on the WSL/Linux filesystem when using WAL.
It contains bounded events and never provider credentials.

```bash
uv run racegreen runs
uv run racegreen run-show RUN_ID
uv run racegreen events list RUN_ID
uv run racegreen db verify
uv run racegreen events export RUN_ID --output timeline.jsonl
uv run racegreen report RUN_ID --output report.html
```

The HTML report is self-contained and renders agent/model/test text as inert
escaped text. JSONL is a derived export for compatibility and is not the
runtime source of truth.

Check local prerequisites without contacting a provider:

```bash
uv run racegreen doctor
uv run racegreen init
```

`init` writes defaults only. Credentials must remain in protected environment
injection or an OS credential store.

List RaceGreen-owned run directories before cleanup, then explicitly confirm:

```bash
uv run racegreen clean
uv run racegreen clean --run-id RUN_ID --yes
```

Cleanup only accepts direct run directories containing RaceGreen's ownership
marker. It does not remove source repositories or arbitrary directories.

The supported tool set is read, search, edit, bounded argument-array command
execution, and candidate submission. Tool paths are resolved inside the
assigned worktree, output is bounded, and test-file modifications are
rejected at candidate submission.

## Multi-agent races

```bash
uv run racegreen race /path/to/repository "Fix the failing test" \
  --agents 2 --max-concurrency 2
```

Only a full-suite-confirmed candidate can win. The default integration path
remains reviewable and does not silently commit to the source branch.

## Local inference

Local inference is optional. On a system with an RTX 4050 and 16 GB RAM, use a
single 7B coder model with 4-bit quantization and concurrency one. Qwen2.5
Coder 7B Instruct is a suitable starting point with Ollama or llama.cpp.
Local inference is not required for the provider or runner tests.
