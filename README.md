# RaceGreen

RaceGreen is a Python CLI for running multiple coding agents on the same task
in isolated Git worktrees. It preserves the user's repository, verifies
candidate patches, requires full-suite confirmation before declaring a winner,
and produces a reviewable result.

The project prioritizes correctness, isolation, reproducible evidence, bounded
cost, and safe cleanup. It is being built with Python 3.12, `uv`, `ruff`,
`mypy`, `pytest`, `asyncio`, SQLite, and Typer. The first agent integration
will use RaceGreen's own tool loop with one OpenAI-compatible provider.

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
