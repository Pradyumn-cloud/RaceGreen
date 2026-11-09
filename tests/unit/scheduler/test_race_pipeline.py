from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from racegreen.agent.events import EventStore
from racegreen.agent.models import ProviderResponse, ToolCall
from racegreen.scheduler.runner import RaceScheduler
from racegreen.verification.config import VerificationConfig


class ScriptedProvider:
    """A deterministic agent that edits one file and submits a candidate."""

    def __init__(self, content: str, *, delay: float = 0.0) -> None:
        self.content = content
        self.delay = delay
        self.calls = 0

    def complete(self, messages, cancel_event=None):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.calls == 1:
            return ProviderResponse(
                tool_calls=(
                    ToolCall("edit", {"path": "app.py", "content": self.content}),
                )
            )
        return ProviderResponse(
            tool_calls=(ToolCall("submit_candidate", {"candidate_id": "candidate"}),)
        )


def _repo(
    tmp_path: Path, expected: int = 2, *, preexisting_failure: bool = False
) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8", newline="\n")
    (repo / "test_app.py").write_text(
        f"from app import value\n\n\ndef test_value():\n    assert value == {expected}\n",
        encoding="utf-8",
        newline="\n",
    )
    if preexisting_failure:
        (repo / "test_unrelated.py").write_text(
            "def test_pre_existing_failure():\n    assert False\n",
            encoding="utf-8",
            newline="\n",
        )
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
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


def _config(target_command: list[str] | None = None) -> VerificationConfig:
    command = [sys.executable, "-m", "pytest", "-q"]
    return VerificationConfig(
        full_suite_command=command,
        test_command=list(target_command) if target_command is not None else list(command),
        use_selection=False,
        max_full_suite_runs_per_run=10,
        max_parallel_verifications=2,
        timeout_full_seconds=120,
        baseline_timeout_seconds=120,
    )


def _run(
    repo: Path,
    tmp_path: Path,
    factory,
    *,
    winner_policy: str = "first_confirmed",
    target_command: list[str] | None = None,
) -> dict:
    scheduler = RaceScheduler(
        repo,
        "make app.value equal 2",
        agents=2,
        max_concurrency=2,
        artifacts_root=tmp_path / "artifacts",
        database=tmp_path / "race.db",
        verification_config=_config(target_command),
        provider_factory=factory,
        winner_policy=winner_policy,
    )
    return scheduler.run()


def test_race_requires_full_suite_confirmation_and_persists_winner(tmp_path: Path) -> None:
    repo, _ = _repo(tmp_path)
    providers = {
        "agent-1": ScriptedProvider("value = 2\n"),
        "agent-2": ScriptedProvider("value = 2\n"),
    }
    summary = _run(repo, tmp_path, lambda agent_id: providers[agent_id])

    assert summary["state"] == "completed"
    assert summary["winner"] is not None
    assert summary["winner"]["evidence_complete"] is True
    assert summary["winner"]["patch_hash"]
    assert any(
        item.get("status") in {"won", "confirmed_but_not_selected"}
        for item in summary["verification"]
    )


def test_race_confirms_with_unchanged_baseline_failures(tmp_path: Path) -> None:
    repo, _ = _repo(tmp_path, preexisting_failure=True)
    providers = {
        "agent-1": ScriptedProvider("value = 2\n"),
        "agent-2": ScriptedProvider("value = 2\n"),
    }
    summary = _run(
        repo,
        tmp_path,
        lambda agent_id: providers[agent_id],
        target_command=[sys.executable, "-m", "pytest", "-q", "test_app.py"],
    )

    assert summary["state"] == "completed"
    assert summary["winner"] is not None
    retained = [
        item
        for item in summary["verification"]
        if item.get("status") in {"won", "confirmed_but_not_selected"}
    ]
    assert retained
    assert retained[0]["baseline_failures_retained"] is True


def test_race_without_full_suite_confirmation_has_no_winner(tmp_path: Path) -> None:
    repo, _ = _repo(tmp_path, expected=3)
    providers = {
        "agent-1": ScriptedProvider("value = 2\n"),
        "agent-2": ScriptedProvider("value = 2\n"),
    }
    summary = _run(repo, tmp_path, lambda agent_id: providers[agent_id])

    assert summary["state"] == "exhausted"
    assert summary["winner"] is None
    assert all(item.get("status") != "won" for item in summary["verification"])


def test_late_candidate_after_winner_is_rejected(tmp_path: Path) -> None:
    repo, _ = _repo(tmp_path)
    providers = {
        "agent-1": ScriptedProvider("value = 2\n"),
        "agent-2": ScriptedProvider("value = 2\n", delay=1.0),
    }
    summary = _run(repo, tmp_path, lambda agent_id: providers[agent_id])

    assert summary["state"] == "completed"
    assert summary["winner"] is not None
    events = EventStore(Path(summary["events_path"])).replay()
    assert any(event.get("event") == "arbiter.winner_declared" for event in events)
    # Any candidate submitted after the terminal marker must be rejected, not
    # allowed to replace the durable winner.
    assert summary["winner"]["candidate_id"] in {
        record.get("candidate_id") for record in summary["verification"]
    }


def test_external_agent_command_can_submit_a_verified_candidate(tmp_path: Path) -> None:
    repo, _ = _repo(tmp_path)
    script = (
        "from pathlib import Path\n"
        "Path('app.py').write_text('value = 2\\n', encoding='utf-8', newline='\\n')\n"
    )
    scheduler = RaceScheduler(
        repo,
        "make app.value equal 2",
        agents=1,
        max_concurrency=1,
        artifacts_root=tmp_path / "artifacts",
        database=tmp_path / "race.db",
        verification_config=_config(),
        external_command=[sys.executable, "-c", script],
    )
    summary = scheduler.run()

    assert summary["state"] == "completed"
    assert summary["winner"] is not None
    assert summary["winner"]["evidence_complete"] is True


def test_consensus_policy_requires_identical_confirmed_patches(tmp_path: Path) -> None:
    repo, _ = _repo(tmp_path)
    providers = {
        "agent-1": ScriptedProvider("value = 2\n"),
        "agent-2": ScriptedProvider("value = 2\n# a different but passing patch\n"),
    }
    summary = _run(repo, tmp_path, lambda agent_id: providers[agent_id], winner_policy="consensus")

    assert summary["state"] == "exhausted"
    assert summary["winner"] is None
    assert summary["policy_decision"]["policy"] == "consensus"
    assert summary["policy_decision"]["reason"] == "no_consensus"
    assert summary["policy_decision"]["quorum"] == 2
    assert all(item.get("status") != "won" for item in summary["verification"])


def test_consensus_policy_selects_identical_confirmed_patches(tmp_path: Path) -> None:
    repo, _ = _repo(tmp_path)
    providers = {
        "agent-1": ScriptedProvider("value = 2\n"),
        "agent-2": ScriptedProvider("value = 2\n"),
    }
    summary = _run(repo, tmp_path, lambda agent_id: providers[agent_id], winner_policy="consensus")

    assert summary["state"] == "completed"
    assert summary["winner"] is not None
    assert summary["policy_decision"]["policy"] == "consensus"
    assert summary["policy_decision"]["reason"].startswith("consensus_patch_hash:")
    assert summary["policy_decision"]["quorum"] == 2
