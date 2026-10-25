from __future__ import annotations

import json
import signal
import sys
from pathlib import Path

import typer

from .agent.config import ProviderConfig
from .agent.events import EventStore
from .agent.models import AgentContext, AgentLimits
from .agent.provider import OpenAICompatibleProvider
from .agent.runner import AgentRunner
from .scheduler.budgets import RunBudgets
from .scheduler.runner import RaceScheduler
from .workspace.manager import WorkspaceManager

app = typer.Typer(no_args_is_help=True)
replay_app = typer.Typer(no_args_is_help=True)


@app.command("agent-run")
def agent_run(
    repository: Path = typer.Argument(..., exists=True, file_okay=False),  # noqa: B008
    task: str = typer.Argument(...),
    artifacts_root: Path | None = typer.Option(None, "--artifacts-root"),  # noqa: B008
    timeout_seconds: float = typer.Option(300.0, min=1.0),
    max_tool_calls: int = typer.Option(100, min=1),
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
    except Exception:
        pass

    try:
        summary = scheduler.run()
        state = summary.get("state")
        typer.echo(json.dumps(summary, sort_keys=True))
        if state == "completed":
            raise typer.Exit(code=0)
        elif state == "cancelled":
            raise typer.Exit(code=130)
        elif state == "exhausted":
            raise typer.Exit(code=1)
        elif state == "failed":
            raise typer.Exit(code=1)
        else:
            raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(json.dumps({"error": str(e)}, sort_keys=True), err=True)
        raise typer.Exit(code=1)


@replay_app.command("show")
def replay_events(path: Path = typer.Argument(..., exists=True, dir_okay=False)) -> None:  # noqa: B008
    """Replay persisted agent events as JSON."""
    for event in EventStore(path).replay():
        typer.echo(json.dumps(event, sort_keys=True))
