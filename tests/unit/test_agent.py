from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from racegreen.agent.events import EventStore
from racegreen.agent.external import ExternalAgentProvider
from racegreen.agent.models import (
    AgentContext,
    AgentLimits,
    AgentStatus,
    ProviderResponse,
    ToolCall,
)
from racegreen.agent.runner import AgentRunner


class ScriptedProvider:
    def __init__(self, responses: list[ProviderResponse]) -> None:
        self.responses = iter(responses)

    def complete(self, messages: list[dict[str, str]]) -> ProviderResponse:
        return next(self.responses)


class DelayedProvider:
    def complete(self, messages: list[dict[str, str]]) -> ProviderResponse:
        time.sleep(0.2)
        return ProviderResponse(content="late")


class SlowProvider:
    def complete(self, messages: list[dict[str, str]]) -> ProviderResponse:
        time.sleep(2.0)
        return ProviderResponse(content="too late")


class UnexpectedErrorProvider:
    def complete(self, messages: list[dict[str, str]]) -> ProviderResponse:
        raise KeyError("unexpected provider failure")


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_app.py").write_text("def test_app(): pass\n", encoding="utf-8")
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "base",
        ],
        check=True,
        capture_output=True,
    )
    base = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return repo, base


def test_agent_submits_hashed_candidate_and_replays_events(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    provider = ScriptedProvider(
        [
            ProviderResponse(tool_calls=(ToolCall("edit", {"path": "app.py", "content": "value = 2\n"}),)),
            ProviderResponse(
                tool_calls=(ToolCall("submit_candidate", {"candidate_id": "candidate-1"}),)
            ),
        ]
    )
    events = EventStore(tmp_path / "events.jsonl")
    context = AgentContext("run-1", "agent-1", str(repo), "Fix app.py")

    status = AgentRunner(provider, events).run(
        context, base_commit=base, candidate_dir=tmp_path / "candidates"
    )

    assert status == AgentStatus.SUBMITTED
    candidates = list((tmp_path / "candidates").glob("candidate-1-*.patch"))
    assert len(candidates) == 1
    assert candidates[0].is_file()
    records = events.replay()
    assert records[-1]["status"] == AgentStatus.SUBMITTED
    assert any(record["event"] == "candidate_submitted" for record in records)


def test_agent_rejects_test_changes(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    provider = ScriptedProvider(
        [
            ProviderResponse(
                tool_calls=(
                    ToolCall(
                        "edit",
                        {"path": "tests/test_app.py", "content": "def test_app(): assert False\n"},
                    ),
                )
            ),
            ProviderResponse(tool_calls=(ToolCall("submit_candidate", {}),)),
        ]
    )
    status = AgentRunner(provider, EventStore(tmp_path / "events.jsonl")).run(
        AgentContext("run-1", "agent-1", str(repo), "Fix app"),
        base_commit=base,
        candidate_dir=tmp_path / "candidates",
    )
    assert status == AgentStatus.PROVIDER_FAILED


def test_cancellation_rejects_late_provider_response(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    events = EventStore(tmp_path / "events.jsonl")
    runner = AgentRunner(DelayedProvider(), events)
    status_holder: list[AgentStatus] = []
    thread = threading.Thread(
        target=lambda: status_holder.append(
            runner.run(
                AgentContext("run-1", "agent-1", str(repo), "Fix app"),
                base_commit=base,
                candidate_dir=tmp_path / "candidates",
            )
        )
    )
    thread.start()
    time.sleep(0.05)
    runner.cancel()
    thread.join(timeout=1)
    assert status_holder == [AgentStatus.CANCELLED]
    assert any(event["event"] == "late_response_rejected" for event in events.replay())


def test_terminal_event_cancels_an_inflight_provider_call(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    events = EventStore(tmp_path / "events.jsonl")
    runner = AgentRunner(DelayedProvider(), events)
    status_holder: list[AgentStatus] = []
    thread = threading.Thread(
        target=lambda: status_holder.append(
            runner.run(
                AgentContext("run-1", "agent-1", str(repo), "Fix app"),
                base_commit=base,
                candidate_dir=tmp_path / "candidates",
            )
        )
    )
    thread.start()
    time.sleep(0.05)
    events.mark_terminal()
    thread.join(timeout=1)

    assert status_holder == [AgentStatus.CANCELLED]


def test_provider_call_is_bounded_by_agent_timeout(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    context = AgentContext(
        "run-1",
        "agent-1",
        str(repo),
        "Fix app",
        limits=AgentLimits(timeout_seconds=0.1),
    )
    started = time.monotonic()

    status = AgentRunner(SlowProvider(), EventStore(tmp_path / "events.jsonl")).run(
        context,
        base_commit=base,
        candidate_dir=tmp_path / "candidates",
    )

    assert status == AgentStatus.TIMED_OUT
    assert time.monotonic() - started < 1.0


def test_unexpected_provider_error_does_not_hang_agent(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    with pytest.raises(KeyError):
        AgentRunner(
            UnexpectedErrorProvider(), EventStore(tmp_path / "events.jsonl")
        ).run(
            AgentContext("run-1", "agent-1", str(repo), "Fix app"),
            base_commit=base,
            candidate_dir=tmp_path / "candidates",
        )


def test_external_agent_receives_task_context(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    script = tmp_path / "agent.py"
    script.write_text(
        "import os, pathlib\n"
        "pathlib.Path('task.txt').write_text(os.environ['RACEGREEN_TASK'], encoding='utf-8')\n",
        encoding="utf-8",
    )
    provider = ExternalAgentProvider(
        [sys.executable, str(script)],
        worktree=worktree,
        run_id="run-1",
        agent_id="agent-1",
    )
    response = provider.complete([{"role": "user", "content": "Fix the failing test"}])

    assert (worktree / "task.txt").read_text(encoding="utf-8") == "Fix the failing test"
    assert response.tool_calls[0].name == "submit_candidate"


def test_event_store_redacts_secret_like_values(tmp_path: Path) -> None:
    events = EventStore(tmp_path / "events.jsonl")
    events.append(
        "agent_failed",
        run_id="run-1",
        agent_id="agent-1",
        error={
            "message": (
                "api_key=top-secret token: another-secret "
                "authorization: Bearer third-secret"
            )
        },
    )

    events.append(
        "agent_failed",
        run_id="run-2",
        agent_id="agent-1",
        argv=("tool", "--api-key", "fourth-secret"),
        config={"api_key": "fifth-secret", "api_key_env": "RACEGREEN_API_KEY"},
    )

    record = events.replay()[-1]
    assert "top-secret" not in str(record)
    assert "another-secret" not in str(record)
    assert "third-secret" not in str(record)
    assert "fourth-secret" not in str(record)
    assert "fifth-secret" not in str(record)
    assert record["config"]["api_key_env"] == "RACEGREEN_API_KEY"
    assert "[REDACTED]" in str(record)


def test_event_store_rejects_malformed_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text('{"event": "ok"}\nnot-json\n', encoding="utf-8")

    with pytest.raises(RuntimeError, match="Unable to replay events"):
        EventStore(path).replay()
