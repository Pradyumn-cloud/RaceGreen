from __future__ import annotations

import subprocess
from pathlib import Path

from racegreen.agent.events import EventStore
from racegreen.agent.models import AgentContext, AgentStatus, ProviderResponse, ToolCall
from racegreen.agent.runner import AgentRunner


class ScriptedProvider:
    def __init__(self, responses: list[ProviderResponse]) -> None:
        self.responses = iter(responses)

    def complete(self, messages: list[dict[str, str]]) -> ProviderResponse:
        return next(self.responses)


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
    candidate = tmp_path / "candidates" / "candidate-1.patch"
    assert candidate.is_file()
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
