# RaceGreen

RaceGreen is a Python CLI for running multiple coding agents on the same task
in isolated Git worktrees. It preserves the user's repository, verifies
candidate patches, requires full-suite confirmation before declaring a winner,
and produces a reviewable result.

The project prioritizes correctness, isolation, reproducible evidence, bounded
cost, and safe cleanup. It is being built with Python 3.12, `uv`, `ruff`,
`mypy`, `pytest`, `asyncio`, SQLite, and Typer. The first agent integration
will use RaceGreen's own tool loop with one OpenAI-compatible provider.
