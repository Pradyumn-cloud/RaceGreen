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

- run search, state filters, and KPI summary cards
- candidate lifecycle stages with patch inspection
- winner evidence, including whether full-suite confirmation was recorded
- a live event timeline with server-sent updates, filters, and follow mode
- copyable CLI commands for starting a run
- responsive desktop and mobile layouts

It remains read-only: the browser cannot start runs, apply patches, or execute
repository commands. Candidate patches are served only when their recorded path
is inside the configured `--artifacts-root` run directory, and the endpoint caps
patch size at 2 MiB. Apply or branch operations remain explicit CLI operations
while their target-state and patch checks are being hardened.

Use the same artifacts root that created the runs:

```bash
uv run racegreen dashboard --artifacts-root "$HOME/.racegreen/runs"
```

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
