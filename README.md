# RaceGreen

RaceGreen is a Python CLI for running multiple coding agents on the same task
in isolated Git worktrees. It preserves the user's repository, verifies
candidate patches, requires full-suite confirmation before declaring a winner,
and produces a reviewable result.

The project prioritizes correctness, isolation, reproducible evidence, bounded
cost, and safe cleanup. It is being built with Python 3.12, `uv`, `ruff`,
`mypy`, `pytest`, `asyncio`, SQLite, and Typer. The first agent integration
will use RaceGreen's own tool loop with one OpenAI-compatible provider.

## Quick setup

Configure a hosted or local OpenAI-compatible provider without manually
exporting every setting:

```bash
uv run racegreen setup
uv run racegreen doctor
uv run racegreen provider-test
```

For Ollama, start Ollama normally on Windows or Linux, then choose `ollama`
in the setup wizard. RaceGreen does not require an API key for local
providers. It does not automatically download models or start services.

Inspect persisted runs in the local dashboard:

```bash
uv run racegreen dashboard --open
```

The dashboard binds to loopback by default and provides:

- run search and state filters
- KPI summary cards
- candidate and winner evidence
- live event timeline with automatic refresh
- copyable CLI commands for starting a run
- responsive desktop and mobile layouts

It remains read-only. Apply or branch operations remain explicit CLI
operations while their target-state and patch checks are being hardened.

## Offline demo

The trace and report flow can be exercised without an API key or network:

```bash
uv sync --locked
uv run racegreen demo
```

The command writes a small SQLite trace and self-contained HTML report under
`.racegreen-demo/`. Do not use the demo as evidence of agent quality; it only
checks the local persistence and presentation path.

## Security limitation

Agent commands run with the current user's permissions. Git worktrees isolate
version-control state but are not an OS sandbox, and agent command network
access is not disabled by RaceGreen alone. Use a container or VM for
untrusted repositories. See [SECURITY.md](SECURITY.md).
