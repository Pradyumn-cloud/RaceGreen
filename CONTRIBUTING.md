# Contributing

RaceGreen handles untrusted model output and must preserve user repositories.
Read `AGENTS.md` and `SECURITY.md` before changing verification, workspaces,
scheduling, storage, or integration code.

## Development setup

```bash
uv sync --locked
uv run racegreen doctor
uv run pytest -q
uv run ruff check . --select E4,E7,E9,F,I
uv run mypy src/racegreen
```

Windows/WSL note: keep clones, worktrees, the SQLite database, and artifacts on
the WSL Linux filesystem rather than `/mnt/c`.

## Change requirements

- Every candidate must be an immutable patch snapshot with a content hash.
- Only a successful configured full-suite confirmation may produce `WON`.
- A selected-test pass, an agent claim, or a model review is never sufficient.
- Winner application is a separate operation from winner declaration.
- New external inputs, process launches, paths, and configuration values must
  be validated at the boundary.
- Do not add a dependency without documenting why it is needed, its maintenance
  status, license, and security impact in the pull request.

## Tests

Add checks at the narrowest useful layer. Verification changes need failure-path
tests; scheduler changes need cancellation and late-event tests; storage changes
need migration and redaction tests. Report exactly which checks were run.

Development-only tooling currently includes `pytest`, `ruff`, `mypy`, and
`pyyaml` (benchmark fixtures). CI also runs `pip-audit`, `gitleaks`, a Python
3.12/3.13 test matrix, and coverage through `pytest-cov` supplied at runtime.
