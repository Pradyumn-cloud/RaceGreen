from __future__ import annotations

import json
import os
import shutil
import signal
import sys
from pathlib import Path

import typer

from .agent.config import ProviderConfig
from .agent.events import EventStore
from .agent.models import AgentContext, AgentLimits
from .agent.provider import OpenAICompatibleProvider
from .agent.runner import AgentRunner
from .report import write_report
from .scheduler.budgets import RunBudgets
from .scheduler.runner import RaceScheduler
from .storage.db import RaceDB
from .storage.security import safe_terminal_text
from .workspace.manager import WorkspaceManager

app = typer.Typer(no_args_is_help=True)
replay_app = typer.Typer(no_args_is_help=True)
db_app = typer.Typer(no_args_is_help=True)
events_app = typer.Typer(no_args_is_help=True)
DEFAULT_DB = Path.home() / ".racegreen" / "racegreen.db"
app.add_typer(db_app, name="db")
app.add_typer(events_app, name="events")


@app.command("agent-run")
def agent_run(
    repository: Path = typer.Argument(..., exists=True, file_okay=False),  # noqa: B008
    task: str = typer.Argument(...),
    artifacts_root: Path | None = typer.Option(None, "--artifacts-root"),  # noqa: B008
    timeout_seconds: float = typer.Option(300.0, min=1.0),
    max_tool_calls: int = typer.Option(100, min=1),
    database: Path = typer.Option(DEFAULT_DB, "--database"),  # noqa: B008
) -> None:
    """Run one bounded coding agent in a private worktree."""
    config = ProviderConfig.from_environment()
    manager = WorkspaceManager(repository, artifacts_root)
    layout = manager.create_run()
    workspace = manager.create_agent_workspace(layout, "agent-1")
    events = EventStore(layout.artifacts / "events.jsonl")
    events.append(
        "run_configured",
        run_id=layout.run_id,
        agent_id=workspace.agent_id,
        base_commit=layout.base.head,
        config_fingerprint=config.fingerprint(),
        model=config.model,
    )
    runner = AgentRunner(
        OpenAICompatibleProvider(
            endpoint=config.endpoint,
            model=config.model,
            api_key_env=config.api_key_env,
            timeout_seconds=config.timeout_seconds,
        ),
        events,
    )
    status = runner.run(
        AgentContext(
            layout.run_id,
            workspace.agent_id,
            str(workspace.path),
            task,
            AgentLimits(timeout_seconds=timeout_seconds, max_tool_calls=max_tool_calls),
        ),
        base_commit=layout.base.head,
        candidate_dir=layout.artifacts / "candidates",
    )
    _db(database).ingest_events(events.replay())
    typer.echo(
        json.dumps(
            {
                "run_id": layout.run_id,
                "agent_id": workspace.agent_id,
                "status": status,
                "events": str(events.path),
                "artifacts": str(layout.artifacts),
            },
            sort_keys=True,
        )
    )


@app.command("race")
def race(
    repository: Path = typer.Argument(..., exists=True, file_okay=False),  # noqa: B008
    task: str = typer.Argument(...),
    agents: int = typer.Option(2, min=1),
    max_concurrency: int | None = typer.Option(None),
    artifacts_root: Path | None = typer.Option(None, "--artifacts-root"),  # noqa: B008
    timeout_seconds: float = typer.Option(300.0, min=1.0),
    max_tokens_per_run: int | None = typer.Option(None),
    max_cost_usd_per_run: float | None = typer.Option(None),
    database: Path = typer.Option(DEFAULT_DB, "--database"),  # noqa: B008
) -> None:
    """Run bounded multi-agent race."""
    max_c = max_concurrency if max_concurrency is not None else agents
    if agents > max_c:
        typer.echo("error: agents exceeds max-concurrency", err=True)
        raise typer.Exit(code=2)
    budgets = RunBudgets(
        max_agent_wall_time_seconds=timeout_seconds,
        max_tokens_per_run=max_tokens_per_run,
        max_cost_usd_per_run=max_cost_usd_per_run,
    )
    scheduler = RaceScheduler(
        repository,
        task,
        agents=agents,
        max_concurrency=max_c,
        artifacts_root=artifacts_root,
        budgets=budgets,
    )

    def _handle_signal(signum, frame):  # type: ignore
        scheduler.cancel()
        raise typer.Exit(code=130)

    try:
        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)
    except (ValueError, OSError):
        pass

    try:
        summary = scheduler.run()
        event_path = (
            (artifacts_root or Path.home() / ".racegreen" / "runs")
            / str(summary["run_id"])
            / "artifacts"
            / "run_events.jsonl"
        )
        if event_path.is_file():
            _db(database).ingest_events(EventStore(event_path).replay())
        state = summary.get("state")
        typer.echo(json.dumps(summary, sort_keys=True))
        if state == "completed":
            raise typer.Exit(code=0)
        elif state == "cancelled":
            raise typer.Exit(code=130)
        elif state == "exhausted" or state == "failed":
            raise typer.Exit(code=1)
        else:
            raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as e:  # noqa: BLE001
        typer.echo(json.dumps({"error": str(e)}, sort_keys=True), err=True)
        raise typer.Exit(code=1)


@replay_app.command("show")
def replay_events(path: Path = typer.Argument(..., exists=True, dir_okay=False)) -> None:  # noqa: B008
    """Replay persisted agent events as JSON."""
    for event in EventStore(path).replay():
        typer.echo(safe_terminal_text(json.dumps(event, sort_keys=True)))


def _db(path: Path) -> RaceDB:
    return RaceDB(path)


@db_app.command("ingest")
def db_ingest(
    events_path: Path = typer.Argument(..., exists=True, dir_okay=False),  # noqa: B008
    database: Path = typer.Option(DEFAULT_DB, "--database"),  # noqa: B008
) -> None:
    """Import an existing JSONL event file into the authoritative trace database."""
    records = EventStore(events_path).replay()
    count = _db(database).ingest_events(records)
    typer.echo(json.dumps({"database": str(database), "events_added": count}, sort_keys=True))


@db_app.command("verify")
def db_verify(
    database: Path = typer.Option(DEFAULT_DB, "--database"),  # noqa: B008
) -> None:
    """Check SQLite integrity and event sequence invariants."""
    result = _db(database).verify()
    typer.echo(json.dumps(result, sort_keys=True))
    if result["violations"]:
        raise typer.Exit(code=1)


@events_app.command("list")
def events_list(
    run_id: str = typer.Argument(...),
    database: Path = typer.Option(DEFAULT_DB, "--database"),  # noqa: B008
    after: int = typer.Option(0, min=0),
    limit: int = typer.Option(100, min=1, max=10000),
) -> None:
    """List persisted events for a run."""
    for event in _db(database).events(run_id, after=after, limit=limit):
        typer.echo(safe_terminal_text(json.dumps(event, sort_keys=True)))


@events_app.command("export")
def events_export(
    run_id: str = typer.Argument(...),
    output: Path = typer.Option(Path("-"), "--output"),  # noqa: B008
    database: Path = typer.Option(DEFAULT_DB, "--database"),  # noqa: B008
) -> None:
    """Export a derived JSONL timeline from SQLite."""
    lines = "\n".join(
        safe_terminal_text(json.dumps(event, sort_keys=True))
        for event in _db(database).events(run_id, limit=10000)
    ) + "\n"
    if str(output) == "-":
        typer.echo(lines, nl=False)
    else:
        output.expanduser().resolve().write_text(lines, encoding="utf-8")


@app.command("runs")
def runs(
    database: Path = typer.Option(DEFAULT_DB, "--database"),  # noqa: B008
) -> None:
    """List runs recorded in SQLite."""
    typer.echo(json.dumps(_db(database).list_runs(), sort_keys=True))


@app.command("run-show")
def run_show(
    run_id: str = typer.Argument(...),
    database: Path = typer.Option(DEFAULT_DB, "--database"),  # noqa: B008
) -> None:
    """Show the persisted run snapshot."""
    typer.echo(json.dumps(_db(database).run_snapshot(run_id), sort_keys=True))


@app.command("report")
def report(
    run_id: str = typer.Argument(...),
    output: Path = typer.Option(Path("racegreen-report.html"), "--output"),  # noqa: B008
    database: Path = typer.Option(DEFAULT_DB, "--database"),  # noqa: B008
) -> None:
    """Write a self-contained HTML report from SQLite."""
    typer.echo(str(write_report(_db(database), run_id, output)))


@app.command("demo")
def demo(
    artifacts_root: Path = typer.Option(
        Path(".racegreen-demo"), "--artifacts-root"
    ),  # noqa: B008
) -> None:
    """Run an offline trace/report demo without credentials or network access."""
    root = artifacts_root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    database = root / "demo.db"
    run_id = "demo-run"
    db = _db(database)
    events = (
        ("run_configured", "scheduler", {"base_commit": "demo-base", "model": "recorded-demo"}),
        ("agent_started", "agent-1", {"status": "working"}),
        ("candidate_frozen", "agent-1", {"candidate_id": "candidate-1", "patch_hash": "demo-hash"}),
        ("verification_finished", "verifier", {
            "candidate_id": "candidate-1",
            "stage": "full_suite",
            "status": "passed",
        }),
        ("run_finished", "scheduler", {"status": "completed"}),
    )
    for event, agent_id, data in events:
        db.append_event(
            {
                "event": event,
                "run_id": run_id,
                "agent_id": agent_id,
                "timestamp": "2026-01-01T00:00:00Z",
                **data,
            }
        )
    output = root / "report.html"
    write_report(db, run_id, output)
    typer.echo(json.dumps({"database": str(database), "report": str(output), "run_id": run_id}))


@app.command("doctor")
def doctor() -> None:
    """Check local prerequisites without contacting a provider."""
    checks = {
        "python_312": sys.version_info[:2] == (3, 12),
        "git": shutil.which("git") is not None,
        "provider_configured": bool(
            os.environ.get("RACEGREEN_API_KEY")
            and os.environ.get("RACEGREEN_MODEL")
        ),
        "provider_url_configured": bool(os.environ.get("RACEGREEN_PROVIDER_URL")),
        "home_writable": os.access(Path.home(), os.W_OK),
    }
    typer.echo(json.dumps({"checks": checks, "ok": all(checks.values())}, sort_keys=True))
    if not checks["python_312"] or not checks["git"] or not checks["home_writable"]:
        raise typer.Exit(code=1)


@app.command("init")
def init(
    config: Path = typer.Option(
        Path.home() / ".config" / "racegreen" / "config.json", "--config"
    ),  # noqa: B008
) -> None:
    """Create a safe local configuration without embedding credentials."""
    destination = config.expanduser().resolve()
    if destination.exists():
        raise typer.BadParameter(f"configuration already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(
            {
                "provider_url": "https://api.openai.com",
                "model": "set-via-environment",
                "artifacts_root": str(Path.home() / ".racegreen" / "runs"),
                "network_policy": "provider-only",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    try:
        destination.chmod(0o600)
    except OSError:
        pass
    typer.echo(str(destination))
