from __future__ import annotations

import json
from pathlib import Path

import typer

from .agent.config import ProviderConfig
from .agent.events import EventStore
from .agent.models import AgentContext, AgentLimits
from .agent.provider import OpenAICompatibleProvider
from .agent.runner import AgentRunner
from .workspace.manager import WorkspaceManager

app = typer.Typer(no_args_is_help=True)


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
