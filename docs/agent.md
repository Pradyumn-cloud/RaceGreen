# Phase 2 single-agent path

The single-agent command runs one provider conversation in a RaceGreen-owned
Git worktree. The provider can be hosted or local, but the initial adapter
uses the OpenAI-compatible chat-completions protocol.

## Configuration

Credentials are read only from the environment:

```bash
export RACEGREEN_API_KEY="..."
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
uv run racegreen C:\path\to\repository "Fix the failing test"
```

Optional controls:

```bash
uv run racegreen C:\path\to\repository "Fix the failing test" \
  --timeout-seconds 300 \
  --max-tool-calls 100 \
  --artifacts-root C:\path\outside\repository\runs
```

The command creates a private worktree and persists JSONL events and candidate
patches under the run artifact directory. A submitted candidate is only a
patch snapshot; this phase does not declare a winner or apply changes to the
source branch.

The supported tool set is read, search, edit, bounded argument-array command
execution, and candidate submission. Tool paths are resolved inside the
assigned worktree, output is bounded, and test-file modifications are
rejected at candidate submission.

## Local inference

Local inference is optional. On a system with an RTX 4050 and 16 GB RAM, use a
single 7B coder model with 4-bit quantization and concurrency one. Qwen2.5
Coder 7B Instruct is a suitable starting point with Ollama or llama.cpp.
Local inference is not required for the provider or runner tests.
