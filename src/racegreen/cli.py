from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import typer

from .agent.config import (
    ProviderConfig,
    default_config_path,
    is_loopback,
    load_repo_config,
    repo_config_path,
)
from .agent.events import EventStore
from .agent.models import AgentContext, AgentLimits
from .agent.provider import OpenAICompatibleProvider, ProviderError, probe_provider
from .agent.runner import AgentRunner
from .arbiter.integration import IntegrationManager
from .arbiter.policy import POLICIES
from .dashboard import serve as serve_dashboard
from .report import write_report
from .scheduler.budgets import RunBudgets
from .scheduler.runner import RaceScheduler
from .storage.db import RaceDB
from .storage.security import safe_terminal_text
from .verification.config import VerificationConfig
from .workspace.git import inspect_repository
from .workspace.manager import WorkspaceManager
from .workspace.models import RunLayout

app = typer.Typer(no_args_is_help=True)
replay_app = typer.Typer(no_args_is_help=True)
db_app = typer.Typer(no_args_is_help=True)
events_app = typer.Typer(no_args_is_help=True)
winner_app = typer.Typer(no_args_is_help=True)
DEFAULT_DB = Path.home() / ".racegreen" / "racegreen.db"
DEFAULT_ARTIFACTS_ROOT = Path.home() / ".racegreen" / "runs"
app.add_typer(db_app, name="db")
app.add_typer(events_app, name="events")
app.add_typer(winner_app, name="winner")


def _progress_enabled(no_progress: bool) -> bool:
    if no_progress:
        return False
    if os.environ.get("RACEGREEN_NO_PROGRESS", "").strip().lower() in {"1", "true", "yes"}:
        return False
    return sys.stderr.isatty()


@contextmanager
def _status_spinner(message: str, *, enabled: bool) -> Iterator[None]:
    if not enabled:
        yield
        return
    stop = threading.Event()
    frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    start = time.monotonic()

    def run() -> None:
        i = 0
        while not stop.is_set():
            elapsed = int(time.monotonic() - start)
            mins, secs = divmod(elapsed, 60)
            text = f"\r{frames[i % len(frames)]} {message} [{mins:02d}:{secs:02d}]"
            try:
                sys.stderr.write(text)
                sys.stderr.flush()
            except Exception:
                return
            i += 1
            stop.wait(0.1)
        try:
            sys.stderr.write("\r" + " " * (len(message) + 20) + "\r")
            sys.stderr.flush()
        except Exception:
            pass

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=1)


@app.command("agent-run")
def agent_run(
    repository: Path = typer.Argument(..., exists=True, file_okay=False),  # noqa: B008
    task: str = typer.Argument(...),
    artifacts_root: Path | None = typer.Option(None, "--artifacts-root"),  # noqa: B008
    timeout_seconds: float = typer.Option(300.0, min=1.0),
    max_tool_calls: int = typer.Option(100, min=1),
    allow_dirty: bool = typer.Option(
        False, "--allow-dirty", help="Allow running when the source repo has uncommitted changes."
    ),
    no_progress: bool = typer.Option(False, "--no-progress", help="Disable the animated status spinner."),
    database: Path = typer.Option(DEFAULT_DB, "--database"),  # noqa: B008
) -> None:
    """Run one bounded coding agent in a private worktree."""
    config = ProviderConfig.from_environment(repository=repository)
    manager = WorkspaceManager(repository, artifacts_root)
    layout = manager.create_run(allow_dirty=allow_dirty)
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
    provider_timeout = max(config.timeout_seconds, timeout_seconds)
    runner = AgentRunner(
        OpenAICompatibleProvider(
            endpoint=config.endpoint,
            model=config.model,
            api_key_env=config.api_key_env,
            timeout_seconds=provider_timeout,
            requires_api_key=config.requires_api_key,
        ),
        events,
    )
    with _status_spinner(
        f"agent-run {workspace.agent_id} ({config.model})", enabled=_progress_enabled(no_progress)
    ):
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
    agents: int | None = typer.Option(None, min=1),
    max_concurrency: int | None = typer.Option(None),
    artifacts_root: Path | None = typer.Option(None, "--artifacts-root"),  # noqa: B008
    timeout_seconds: float = typer.Option(300.0, min=1.0),
    max_tokens_per_run: int | None = typer.Option(None),
    max_cost_usd_per_run: float | None = typer.Option(None),
    verification_command: str | None = typer.Option(
        None,
        "--verification-command",
        help="Full-suite command as whitespace-separated arguments; no shell is used.",
    ),
    max_full_suite_runs: int | None = typer.Option(None, min=1),
    max_changed_files: int | None = typer.Option(None, "--max-changed-files", min=1),
    max_changed_lines: int | None = typer.Option(None, "--max-changed-lines", min=1),
    max_patch_bytes: int | None = typer.Option(None, "--max-patch-bytes", min=1),
    winner_policy: str | None = typer.Option(
        None,
        "--winner-policy",
        help=f"Winner policy: {', '.join(POLICIES)}.",
        case_sensitive=False,
    ),
    consensus_quorum: int | None = typer.Option(None, "--consensus-quorum", min=1),
    hedge_delay: float | None = typer.Option(
        None, "--hedge-delay", min=0.0, help="Seconds to stagger agent starts."
    ),
    agent_model: list[str] | None = typer.Option(
        None, "--agent-model", help="Model for each agent; repeat to diversify agents."
    ),
    external_agent: str | None = typer.Option(
        None,
        "--external-agent",
        help="External agent command to run once in each worktree (no shell).",
    ),
    sandbox: str | None = typer.Option(
        None, "--sandbox", help="Verification sandbox: none or docker."
    ),
    sandbox_image: str | None = typer.Option(None, "--sandbox-image"),
    sandbox_network: str | None = typer.Option(None, "--sandbox-network"),
    sandbox_memory: str | None = typer.Option(None, "--sandbox-memory"),
    sandbox_cpus: str | None = typer.Option(None, "--sandbox-cpus"),
    require_approval: bool = typer.Option(
        False,
        "--require-approval",
        help="Do not auto-export a winner; require an explicit winner export/branch/apply step.",
    ),
    allow_dirty: bool = typer.Option(
        False, "--allow-dirty", help="Allow running when the source repo has uncommitted changes."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Validate configuration and repository state without starting agents."
    ),
    no_progress: bool = typer.Option(False, "--no-progress", help="Disable the animated status spinner."),
    database: Path = typer.Option(DEFAULT_DB, "--database"),  # noqa: B008
) -> None:
    """Run bounded multi-agent race with real verification and arbitration."""
    repo_config = load_repo_config(repository)
    try:
        provider_config = ProviderConfig.from_environment(repository=repository)
    except ValueError:
        provider_config = None
    local_provider = provider_config is not None and is_loopback(provider_config.endpoint)

    configured_external = external_agent
    if configured_external is None:
        repo_external = repo_config.get("external_agent")
        configured_external = repo_external if isinstance(repo_external, str) else None
    external_command = shlex.split(configured_external) if configured_external else None
    if external_command is not None and not external_command:
        typer.echo("error: external agent command is empty", err=True)
        raise typer.Exit(code=2)

    configured_agents = agents
    if configured_agents is None:
        repo_agents = repo_config.get("agents")
        configured_agents = int(repo_agents) if isinstance(repo_agents, int) else None
    if configured_agents is None:
        configured_agents = 1 if (local_provider or external_command is not None) else 2
    if configured_agents < 1:
        typer.echo("error: agents must be at least 1", err=True)
        raise typer.Exit(code=2)

    configured_max = max_concurrency
    if configured_max is None:
        repo_max = repo_config.get("max_concurrency")
        configured_max = int(repo_max) if isinstance(repo_max, int) else None
    if configured_max is None:
        configured_max = 1 if local_provider else configured_agents
    max_c = configured_max
    if max_c < 1:
        typer.echo("error: max-concurrency must be at least 1", err=True)
        raise typer.Exit(code=2)
    if configured_agents > max_c:
        typer.echo("error: agents must not exceed max-concurrency", err=True)
        raise typer.Exit(code=2)

    configured_policy = (
        winner_policy
        or (repo_config.get("winner_policy") if isinstance(repo_config.get("winner_policy"), str) else None)
        or "first_confirmed"
    )
    if configured_policy not in POLICIES:
        typer.echo(
            f"error: winner-policy must be one of {', '.join(POLICIES)}",
            err=True,
        )
        raise typer.Exit(code=2)
    configured_quorum = consensus_quorum
    if configured_quorum is None:
        repo_quorum = repo_config.get("consensus_quorum")
        configured_quorum = int(repo_quorum) if isinstance(repo_quorum, int) else None
    configured_hedge = hedge_delay
    if configured_hedge is None:
        repo_hedge = repo_config.get("hedge_delay_seconds")
        configured_hedge = float(repo_hedge) if isinstance(repo_hedge, (int, float)) else 0.0
    configured_models = agent_model
    if configured_models is None:
        repo_models = repo_config.get("agent_models")
        configured_models = list(repo_models) if isinstance(repo_models, list) else None

    configured_artifacts = artifacts_root
    if configured_artifacts is None:
        repo_artifacts = repo_config.get("artifacts_root")
        configured_artifacts = Path(repo_artifacts) if isinstance(repo_artifacts, str) else None

    configured_verification = verification_command
    if configured_verification is None:
        repo_verification = repo_config.get("verification_command")
        configured_verification = repo_verification if isinstance(repo_verification, str) else None

    configured_full_runs = max_full_suite_runs
    if configured_full_runs is None:
        repo_full_runs = repo_config.get("max_full_suite_runs")
        configured_full_runs = int(repo_full_runs) if isinstance(repo_full_runs, int) else 10

    configured_changed_files = max_changed_files
    if configured_changed_files is None:
        repo_changed_files = repo_config.get("max_changed_files")
        configured_changed_files = int(repo_changed_files) if isinstance(repo_changed_files, int) else None
    configured_changed_lines = max_changed_lines
    if configured_changed_lines is None:
        repo_changed_lines = repo_config.get("max_changed_lines")
        configured_changed_lines = int(repo_changed_lines) if isinstance(repo_changed_lines, int) else None
    configured_patch_bytes = max_patch_bytes
    if configured_patch_bytes is None:
        repo_patch_bytes = repo_config.get("max_patch_bytes")
        configured_patch_bytes = int(repo_patch_bytes) if isinstance(repo_patch_bytes, int) else 5 * 1024 * 1024

    configured_sandbox = sandbox
    if configured_sandbox is None:
        repo_sandbox = repo_config.get("sandbox")
        configured_sandbox = repo_sandbox if isinstance(repo_sandbox, str) else "none"
    if configured_sandbox not in {"none", "docker"}:
        typer.echo("error: sandbox must be 'none' or 'docker'", err=True)
        raise typer.Exit(code=2)
    configured_sandbox_image = sandbox_image
    if configured_sandbox_image is None:
        repo_image = repo_config.get("sandbox_image")
        configured_sandbox_image = repo_image if isinstance(repo_image, str) else None
    if configured_sandbox == "docker" and not configured_sandbox_image:
        typer.echo("error: --sandbox-image is required with --sandbox docker", err=True)
        raise typer.Exit(code=2)
    configured_sandbox_network = sandbox_network
    if configured_sandbox_network is None:
        repo_network = repo_config.get("sandbox_network")
        configured_sandbox_network = repo_network if isinstance(repo_network, str) else "none"
    configured_sandbox_memory = sandbox_memory
    if configured_sandbox_memory is None:
        repo_memory = repo_config.get("sandbox_memory")
        configured_sandbox_memory = repo_memory if isinstance(repo_memory, str) else None
    configured_sandbox_cpus = sandbox_cpus
    if configured_sandbox_cpus is None:
        repo_cpus = repo_config.get("sandbox_cpus")
        configured_sandbox_cpus = repo_cpus if isinstance(repo_cpus, str) else None

    budgets = RunBudgets(
        max_agent_wall_time_seconds=timeout_seconds,
        max_tokens_per_run=max_tokens_per_run,
        max_cost_usd_per_run=max_cost_usd_per_run,
    )
    if configured_verification:
        command = shlex.split(configured_verification)
        if not command:
            typer.echo("error: verification command is empty", err=True)
            raise typer.Exit(code=2)
    else:
        command = ["pytest", "-q"]
    verification_config = VerificationConfig(
        full_suite_command=command,
        test_command=list(command),
        use_selection=False,
        max_full_suite_runs_per_run=configured_full_runs,
        max_changed_files=configured_changed_files,
        max_changed_lines=configured_changed_lines,
        max_patch_bytes=configured_patch_bytes,
        sandbox=configured_sandbox,
        sandbox_image=configured_sandbox_image,
        sandbox_network=configured_sandbox_network,
        sandbox_memory=configured_sandbox_memory,
        sandbox_cpus=configured_sandbox_cpus,
    )
    if dry_run:
        head = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        status = subprocess.run(
            ["git", "-C", str(repository), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        dirty = bool(status.stdout.strip())
        external_ready = (
            external_command is None
            or shutil.which(external_command[0]) is not None
        )
        provider_ready = (external_command is not None and external_ready) or (
            provider_config is not None
            and (
                not provider_config.requires_api_key
                or bool(os.environ.get(provider_config.api_key_env))
            )
        )
        command_ready = shutil.which(command[0]) is not None if command else False
        ok = (
            head.returncode == 0
            and (allow_dirty or not dirty)
            and provider_ready
            and external_ready
            and command_ready
            and (configured_sandbox != "docker" or shutil.which("docker") is not None)
        )
        typer.echo(
            json.dumps(
                {
                    "dry_run": True,
                    "ok": ok,
                    "repository": str(repository),
                    "head": head.stdout.strip() or None,
                    "dirty": dirty,
                    "allow_dirty": allow_dirty,
                    "agents": configured_agents,
                    "max_concurrency": max_c,
                    "winner_policy": configured_policy,
                    "consensus_quorum": configured_quorum,
                    "verification_command": command,
                    "verification_command_available": command_ready,
                    "provider_ready": provider_ready,
                    "external_agent": configured_external,
                    "external_agent_available": external_ready,
                    "sandbox": configured_sandbox,
                    "artifacts_root": str(configured_artifacts) if configured_artifacts else None,
                    "database": str(database),
                },
                sort_keys=True,
            )
        )
        if not ok:
            raise typer.Exit(code=1)
        return
    scheduler = RaceScheduler(
        repository,
        task,
        agents=configured_agents,
        max_concurrency=max_c,
        artifacts_root=configured_artifacts,
        budgets=budgets,
        allow_dirty=allow_dirty,
        database=database,
        verification_config=verification_config,
        winner_policy=configured_policy,
        consensus_quorum=configured_quorum,
        hedge_delay_seconds=configured_hedge,
        agent_models=configured_models,
        external_command=external_command,
    )
    approval_required = require_approval or bool(repo_config.get("require_approval", False))

    def _handle_signal(signum, frame):  # type: ignore
        scheduler.cancel()
        raise typer.Exit(code=130)

    try:
        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)
    except (ValueError, OSError):
        pass

    try:
        with _status_spinner(
            f"race {configured_agents} agent(s), max concurrency {max_c}",
            enabled=_progress_enabled(no_progress),
        ):
            summary = scheduler.run()
        event_path = Path(summary.get("events_path") or "")
        database_handle = _db(database)
        if event_path.is_file():
            database_handle.ingest_events(EventStore(event_path).replay())
        winner = database_handle.get_winner(str(summary["run_id"]))
        if approval_required and winner is not None:
            summary["approval_required"] = True
            summary["next_step"] = (
                f"racegreen winner show --run-id {summary['run_id']} "
                f"&& racegreen winner branch --run-id {summary['run_id']}"
            )
        elif winner is not None and winner.get("evidence_complete"):
            patch_value = database_handle.candidate_patch_path(
                str(summary["run_id"]), str(winner["candidate_id"])
            )
            if patch_value:
                run_root = Path(str(summary["artifacts_root"]))
                summary["winner_export"] = IntegrationManager(
                    EventStore(event_path) if event_path.is_file() else None
                ).export_patch_and_evidence(
                    run_root,
                    str(summary["run_id"]),
                    winner,
                    Path(patch_value),
                    str(winner["base_commit"]),
                    task,
                )
        summary["winner"] = winner
        state = summary.get("state")
        typer.echo(json.dumps(summary, sort_keys=True))
        if state == "completed":
            raise typer.Exit(code=0)
        elif state == "cancelled":
            raise typer.Exit(code=130)
        elif state in {"exhausted", "failed", "unverifiable"}:
            raise typer.Exit(code=1)
        else:
            raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as e:  # noqa: BLE001
        typer.echo(json.dumps({"error": str(e)}, sort_keys=True), err=True)
        raise typer.Exit(code=1)


@app.command("resume")
def resume(
    run_id: str = typer.Argument(...),
    database: Path = typer.Option(DEFAULT_DB, "--database"),  # noqa: B008
    dry_run: bool = typer.Option(False, "--dry-run"),
    no_progress: bool = typer.Option(False, "--no-progress"),
    require_approval: bool = typer.Option(True, "--require-approval/--no-require-approval"),
) -> None:
    """Restart an interrupted run as a new attempt from its recorded base commit.

    Candidate snapshots and worktrees from the old attempt are never reused.
    The command refuses if the repository HEAD has moved away from the recorded
    base commit or if the old run already has a winner.
    """
    db = RaceDB(database)
    try:
        snapshot = db.run_snapshot(run_id)
        events = db.events(run_id, limit=1000, event_types=["run_started"])
    except KeyError:
        typer.echo(f"error: unknown run {run_id}", err=True)
        raise typer.Exit(code=2) from None
    if snapshot.get("winner") is not None or snapshot["run"].get("state") == "completed":
        typer.echo("error: this run already has a recorded winner", err=True)
        raise typer.Exit(code=2)
    if not events:
        typer.echo("error: run has no run_started event to resume from", err=True)
        raise typer.Exit(code=2)
    payload = events[0]
    base_commit = str(payload.get("base_commit") or snapshot["run"].get("base_commit") or "")
    repository_value = payload.get("repository")
    if not isinstance(repository_value, str) or not repository_value:
        typer.echo("error: run does not record its repository path", err=True)
        raise typer.Exit(code=2)
    repository = Path(repository_value)
    head = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    current_head = head.stdout.strip()
    if head.returncode != 0 or current_head != base_commit:
        typer.echo(
            "error: repository HEAD no longer matches the run base commit; "
            "start a new race instead",
            err=True,
        )
        raise typer.Exit(code=2)

    full_suite = payload.get("full_suite_command")
    verification_command = (
        shlex.join([str(part) for part in full_suite])
        if isinstance(full_suite, list) and full_suite
        else None
    )
    external = payload.get("external_command")
    external_agent = (
        shlex.join([str(part) for part in external])
        if isinstance(external, list) and external
        else None
    )
    budgets = payload.get("budgets")
    budgets = budgets if isinstance(budgets, dict) else {}
    models = payload.get("agent_models")
    models = [str(model) for model in models] if isinstance(models, list) else None
    artifacts = payload.get("artifacts_root")
    artifacts_root = Path(artifacts) if isinstance(artifacts, str) and artifacts else None

    race(
        repository=repository,
        task=str(payload.get("task") or ""),
        agents=int(payload["agents"]) if isinstance(payload.get("agents"), int) else None,
        max_concurrency=(
            int(payload["max_concurrency"])
            if isinstance(payload.get("max_concurrency"), int)
            else None
        ),
        artifacts_root=artifacts_root,
        timeout_seconds=float(budgets.get("max_agent_wall_time_seconds", 300.0)),
        max_tokens_per_run=(
            int(budgets["max_tokens_per_run"])
            if isinstance(budgets.get("max_tokens_per_run"), int)
            else None
        ),
        max_cost_usd_per_run=(
            float(budgets["max_cost_usd_per_run"])
            if isinstance(budgets.get("max_cost_usd_per_run"), (int, float))
            else None
        ),
        verification_command=verification_command,
        max_full_suite_runs=(
            int(payload["max_full_suite_runs"])
            if isinstance(payload.get("max_full_suite_runs"), int)
            else None
        ),
        max_changed_files=(
            int(payload["max_changed_files"])
            if isinstance(payload.get("max_changed_files"), int)
            else None
        ),
        max_changed_lines=(
            int(payload["max_changed_lines"])
            if isinstance(payload.get("max_changed_lines"), int)
            else None
        ),
        max_patch_bytes=(
            int(payload["max_patch_bytes"])
            if isinstance(payload.get("max_patch_bytes"), int)
            else None
        ),
        winner_policy=(
            str(payload["winner_policy"])
            if isinstance(payload.get("winner_policy"), str)
            else None
        ),
        consensus_quorum=(
            int(payload["consensus_quorum"])
            if isinstance(payload.get("consensus_quorum"), int)
            else None
        ),
        hedge_delay=(
            float(payload["hedge_delay_seconds"])
            if isinstance(payload.get("hedge_delay_seconds"), (int, float))
            else None
        ),
        agent_model=models,
        external_agent=external_agent,
        sandbox=(
            str(payload["sandbox"]) if isinstance(payload.get("sandbox"), str) else None
        ),
        sandbox_image=(
            str(payload["sandbox_image"])
            if isinstance(payload.get("sandbox_image"), str)
            else None
        ),
        sandbox_network=(
            str(payload["sandbox_network"])
            if isinstance(payload.get("sandbox_network"), str)
            else None
        ),
        sandbox_memory=(
            str(payload["sandbox_memory"])
            if isinstance(payload.get("sandbox_memory"), str)
            else None
        ),
        sandbox_cpus=(
            str(payload["sandbox_cpus"])
            if isinstance(payload.get("sandbox_cpus"), str)
            else None
        ),
        require_approval=require_approval,
        allow_dirty=False,
        dry_run=dry_run,
        no_progress=no_progress,
        database=database,
    )


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


@db_app.command("rebuild")
def db_rebuild(
    database: Path = typer.Option(DEFAULT_DB, "--database"),  # noqa: B008
) -> None:
    """Replay the persisted event trace into run, agent, and candidate projections."""
    result = _db(database).rebuild_projections()
    typer.echo(json.dumps(result, sort_keys=True))


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


def _require_winner(database: Path, run_id: str) -> tuple[RaceDB, dict[str, object], Path]:
    db = _db(database)
    winner = db.get_winner(run_id)
    if winner is None:
        typer.echo(json.dumps({"error": "no winner for this run"}, sort_keys=True), err=True)
        raise typer.Exit(code=1)
    if not winner.get("evidence_complete"):
        typer.echo(
            json.dumps({"error": "winner lacks complete full-suite evidence"}, sort_keys=True),
            err=True,
        )
        raise typer.Exit(code=1)
    patch_value = db.candidate_patch_path(run_id, str(winner["candidate_id"]))
    if not patch_value:
        typer.echo(json.dumps({"error": "winner patch is missing"}, sort_keys=True), err=True)
        raise typer.Exit(code=1)
    patch = Path(patch_value).resolve(strict=True)
    if hashlib.sha256(patch.read_bytes()).hexdigest() != winner.get("patch_hash"):
        typer.echo(json.dumps({"error": "winner patch hash mismatch"}, sort_keys=True), err=True)
        raise typer.Exit(code=1)
    return db, winner, patch


@winner_app.command("show")
def winner_show(
    run_id: str = typer.Argument(...),
    database: Path = typer.Option(DEFAULT_DB, "--database"),  # noqa: B008
) -> None:
    """Show the durable winner and its confirmation evidence."""
    _, winner, patch = _require_winner(database, run_id)
    typer.echo(json.dumps({**winner, "patch": str(patch)}, sort_keys=True, default=str))


@winner_app.command("export")
def winner_export(
    run_id: str = typer.Argument(...),
    output: Path | None = typer.Option(None, "--output"),  # noqa: B008
    force: bool = typer.Option(False, "--force"),
    database: Path = typer.Option(DEFAULT_DB, "--database"),  # noqa: B008
) -> None:
    """Export the verified winner patch without modifying the target repository."""
    _, winner, patch = _require_winner(database, run_id)
    destination = (output or Path(f"racegreen-{run_id}-winner.patch")).expanduser().resolve()
    if destination.exists() and not force:
        typer.echo(json.dumps({"error": f"output exists: {destination}"}, sort_keys=True), err=True)
        raise typer.Exit(code=1)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(patch.read_bytes())
    typer.echo(
        json.dumps(
            {
                "status": "ok",
                "run_id": run_id,
                "candidate_id": winner["candidate_id"],
                "patch": str(destination),
                "patch_hash": winner["patch_hash"],
            },
            sort_keys=True,
        )
    )


@winner_app.command("branch")
def winner_branch(
    run_id: str = typer.Argument(...),
    repository: Path = typer.Option(Path("."), "--repository", exists=True, file_okay=False),  # noqa: B008
    database: Path = typer.Option(DEFAULT_DB, "--database"),  # noqa: B008
) -> None:
    """Create a review branch containing the verified winner patch."""
    _, winner, patch = _require_winner(database, run_id)
    result = IntegrationManager().create_branch(
        repository, run_id, winner, str(winner["base_commit"]), patch_path=patch
    )
    typer.echo(json.dumps(result, sort_keys=True, default=str))
    if result.get("status") != "ok":
        raise typer.Exit(code=1)


@winner_app.command("apply")
def winner_apply(
    run_id: str = typer.Argument(...),
    repository: Path = typer.Option(Path("."), "--repository", exists=True, file_okay=False),  # noqa: B008
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
    database: Path = typer.Option(DEFAULT_DB, "--database"),  # noqa: B008
) -> None:
    """Apply the winner patch only if the target is still at the captured base."""
    _, winner, patch = _require_winner(database, run_id)
    if not yes:
        confirmed = typer.confirm(
            f"Apply winner {winner['candidate_id']} to {repository}?", default=False
        )
        if not confirmed:
            typer.echo(json.dumps({"status": "refused", "reason": "not_approved"}, sort_keys=True))
            raise typer.Exit(code=1)
    result = IntegrationManager().apply_patch(
        repository, patch, str(winner["base_commit"]), str(winner["patch_hash"])
    )
    typer.echo(json.dumps(result, sort_keys=True, default=str))
    if result.get("status") != "applied":
        raise typer.Exit(code=1)


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


@app.command("dashboard")
def dashboard(
    database: Path = typer.Option(DEFAULT_DB, "--database"),  # noqa: B008
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8765, "--port", min=0, max=65535),
    open_browser: bool = typer.Option(False, "--open"),
    artifacts_root: Path = typer.Option(  # noqa: B008
        DEFAULT_ARTIFACTS_ROOT, "--artifacts-root"
    ),
) -> None:
    """Open the local run dashboard; it is loopback-only and read-only."""
    try:
        serve_dashboard(
            _db(database),
            host=host,
            port=port,
            open_browser=open_browser,
            artifacts_root=artifacts_root,
        )
    except KeyboardInterrupt:
        typer.echo("Dashboard stopped.")


@app.command("doctor")
def doctor(fix: bool = typer.Option(False, "--fix", help="Create safe directories and a missing config template.")) -> None:
    """Check local prerequisites without contacting a provider."""
    fixes: list[str] = []
    if fix:
        for directory in (Path.home() / ".racegreen", Path.home() / ".racegreen" / "runs"):
            try:
                directory.mkdir(parents=True, exist_ok=True)
                fixes.append(f"ensured {directory}")
            except OSError as exc:
                fixes.append(f"failed {directory}: {exc}")
        active_config = default_config_path()
        if not active_config.exists():
            try:
                active_config.parent.mkdir(parents=True, exist_ok=True)
                active_config.write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "provider_url": "https://api.openai.com",
                            "model": "set-via-environment",
                            "requires_api_key": True,
                            "artifacts_root": str(Path.home() / ".racegreen" / "runs"),
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                try:
                    active_config.chmod(0o600)
                except OSError:
                    pass
                fixes.append(f"created {active_config}")
            except OSError as exc:
                fixes.append(f"failed {active_config}: {exc}")
    try:
        config = ProviderConfig.from_environment()
        provider_error = None
    except ValueError as exc:
        config = None
        provider_error = str(exc)
    provider_configured = config is not None and (
        not config.requires_api_key or bool(os.environ.get(config.api_key_env))
    )
    checks = {
        "python_supported": sys.version_info >= (3, 12),
        "git": shutil.which("git") is not None,
        "ripgrep": shutil.which("rg") is not None,
        "provider_configured": provider_configured,
        "provider_url_configured": config is not None,
        "home_writable": os.access(Path.home(), os.W_OK),
        "docker": shutil.which("docker") is not None,
    }
    required = ("python_supported", "git", "home_writable")
    result: dict[str, object] = {
        "checks": checks,
        "ok": all(checks[key] for key in required),
        "config_path": str(default_config_path()),
        "fixes": fixes,
    }
    if config is not None:
        result["provider"] = {
            "endpoint": config.endpoint,
            "model": config.model,
            "requires_api_key": config.requires_api_key,
        }
    elif provider_error:
        result["provider_error"] = provider_error
    typer.echo(json.dumps(result, sort_keys=True))
    if not all(checks[key] for key in required):
        raise typer.Exit(code=1)


@app.command("setup")
def setup(
    provider: str | None = typer.Option(None, "--provider"),
    model: str | None = typer.Option(None, "--model"),
    endpoint: str | None = typer.Option(None, "--endpoint"),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    """Configure a provider without storing credentials."""
    provider_name = (
        provider
        or typer.prompt(
            "Provider (ollama, lmstudio, vllm, openai, openrouter, groq, together, compatible)",
            default="ollama",
        )
    ).lower()
    defaults = {
        "ollama": ("http://127.0.0.1:11434", False, "qwen2.5-coder:7b"),
        "lmstudio": ("http://127.0.0.1:1234", False, "set-via-environment"),
        "vllm": ("http://127.0.0.1:8000", False, "set-via-environment"),
        "openai": ("https://api.openai.com", True, "set-via-environment"),
        "openrouter": ("https://openrouter.ai/api", True, "set-via-environment"),
        "groq": ("https://api.groq.com/openai", True, "set-via-environment"),
        "together": ("https://api.together.xyz", True, "set-via-environment"),
        "compatible": ("https://api.openai.com", True, "set-via-environment"),
    }
    if provider_name not in defaults:
        raise typer.BadParameter(
            "provider must be one of: " + ", ".join(sorted(defaults))
        )
    default_endpoint, requires_api_key, default_model = defaults[provider_name]
    selected_endpoint = endpoint or typer.prompt("Provider endpoint", default=default_endpoint)
    selected_model = model or typer.prompt("Model name", default=default_model)
    destination = (config or default_config_path()).expanduser().resolve()
    if destination.exists():
        raise typer.BadParameter(f"configuration already exists: {destination}; remove it or pass another path")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "provider": provider_name,
                "provider_url": selected_endpoint,
                "model": selected_model,
                "requires_api_key": requires_api_key,
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
    typer.echo(json.dumps({"config": str(destination), "credentials": "not stored"}))


@app.command("provider-test")
def provider_test(
    repository: Path | None = typer.Option(
        None, "--repository", exists=True, file_okay=False
    ),  # noqa: B008
) -> None:
    """Test the configured provider without sending a model completion."""
    try:
        config = ProviderConfig.from_environment(repository=repository)
        result = probe_provider(config)
        result["model"] = config.model
        result["requires_api_key"] = config.requires_api_key
        typer.echo(json.dumps(result, sort_keys=True))
    except (ProviderError, ValueError) as exc:
        typer.echo(json.dumps({"reachable": False, "error": str(exc)}, sort_keys=True), err=True)
        raise typer.Exit(code=1)


@app.command("init")
def init(
    config: Path | None = typer.Option(None, "--config"),  # noqa: B008
    repository: Path | None = typer.Option(
        None, "--repo", exists=True, file_okay=False, help="Write repository-local configuration."
    ),  # noqa: B008
) -> None:
    """Create a safe local configuration without embedding credentials."""
    if config is not None and repository is not None:
        raise typer.BadParameter("pass either --config or --repo, not both")
    destination = (
        repo_config_path(repository)
        if repository is not None
        else (config or default_config_path()).expanduser().resolve()
    )
    if destination.exists():
        raise typer.BadParameter(f"configuration already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "provider_url": "https://api.openai.com",
                "model": "set-via-environment",
                "requires_api_key": True,
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


@app.command("quickstart")
def quickstart(
    provider: str = typer.Option("ollama", "--provider"),
    model: str | None = typer.Option(None, "--model"),
    endpoint: str | None = typer.Option(None, "--endpoint"),
    config: Path | None = typer.Option(None, "--config"),  # noqa: B008
    force: bool = typer.Option(False, "--force", help="Overwrite an existing configuration."),
    probe: bool = typer.Option(False, "--probe", help="Contact the provider after setup."),
) -> None:
    """Create configuration, run local checks, and print the next commands."""
    presets = {
        "ollama": ("http://127.0.0.1:11434", False, "qwen2.5-coder:7b"),
        "lmstudio": ("http://127.0.0.1:1234", False, "set-via-environment"),
        "vllm": ("http://127.0.0.1:8000", False, "set-via-environment"),
        "openai": ("https://api.openai.com", True, "set-via-environment"),
        "openrouter": ("https://openrouter.ai/api", True, "set-via-environment"),
        "groq": ("https://api.groq.com/openai", True, "set-via-environment"),
        "together": ("https://api.together.xyz", True, "set-via-environment"),
        "compatible": ("https://api.openai.com", True, "set-via-environment"),
    }
    provider_name = provider.lower()
    if provider_name not in presets:
        typer.echo("error: provider must be one of: " + ", ".join(sorted(presets)), err=True)
        raise typer.Exit(code=2)
    default_endpoint, requires_api_key, default_model = presets[provider_name]
    selected_model = model or default_model
    if selected_model == "set-via-environment":
        typer.echo("error: pass --model for hosted providers", err=True)
        raise typer.Exit(code=2)
    destination = (config or default_config_path()).expanduser().resolve()
    if destination.exists() and not force:
        typer.echo(f"configuration already exists: {destination}", err=True)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "provider": provider_name,
                    "provider_url": endpoint or default_endpoint,
                    "model": selected_model,
                    "requires_api_key": requires_api_key,
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
    doctor(fix=True)
    if probe:
        try:
            provider_test(repository=None)
        except typer.Exit:
            pass
    typer.echo(
        json.dumps(
            {
                "config": str(destination),
                "next": [
                    "racegreen doctor",
                    "racegreen race <repository> <task> --agents 2 --verification-command \"pytest -q\"",
                    "racegreen runs",
                ],
                "credentials": "not stored; set the provider environment variable",
            },
            sort_keys=True,
        )
    )


@app.command("clean")
def clean(
    run_id: str | None = typer.Option(None, "--run-id"),
    artifacts_root: Path = typer.Option(
        Path.home() / ".racegreen" / "runs", "--artifacts-root"
    ),  # noqa: B008
    confirm: bool = typer.Option(False, "--yes"),
) -> None:
    """List or remove only explicitly-owned RaceGreen run directories."""
    root = artifacts_root.expanduser().resolve()
    if not root.is_dir():
        typer.echo(json.dumps({"runs": [], "removed": []}))
        return
    candidates = [root / run_id] if run_id else sorted(root.iterdir())
    owned: list[Path] = []
    for candidate in candidates:
        if (
            candidate.parent == root
            and candidate.is_dir()
            and (candidate / "ownership.json").is_file()
        ):
            owned.append(candidate)
    if not confirm:
        typer.echo(json.dumps({"runs": [path.name for path in owned], "removed": []}))
        return
    removed: list[str] = []
    errors: list[str] = []
    for candidate in owned:
        manifest_path = candidate / "ownership.json"
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
        if not isinstance(payload, dict) or not payload.get("repository"):
            # Legacy or malformed marker: preserve the historical behavior for
            # explicitly confirmed cleanup, but do not infer Git ownership.
            shutil.rmtree(candidate)
            removed.append(candidate.name)
            continue
        try:
            repository = inspect_repository(Path(str(payload["repository"])))
            manager = WorkspaceManager(repository.root, root)
            layout = RunLayout(
                str(payload["run_id"]),
                candidate,
                candidate / "artifacts",
                candidate / "worktrees",
                repository,
            )
            cleanup_errors = manager.cleanup(layout)
        except Exception as exc:  # noqa: BLE001 - report and retain evidence
            errors.append(f"{candidate.name}: {type(exc).__name__}: {exc}")
            continue
        if cleanup_errors:
            errors.extend(f"{candidate.name}: {item}" for item in cleanup_errors)
        else:
            removed.append(candidate.name)
    typer.echo(
        json.dumps(
            {"runs": [path.name for path in owned], "removed": removed, "errors": errors}
        )
    )
