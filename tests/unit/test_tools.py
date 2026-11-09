from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

from racegreen.agent.tools import ToolError, WorkspaceTools
from racegreen.verification.process import BoundedCommandResult


def test_command_cancellation_terminates_process(tmp_path: Path) -> None:
    cancellation = threading.Event()
    tools = WorkspaceTools(
        tmp_path, timeout_seconds=10, max_output_bytes=1000, cancel_event=cancellation
    )
    cancellation.set()
    with pytest.raises(ToolError, match="cancelled"):
        tools.command(["python", "-c", "print('should not run')"])


def test_path_traversal_is_rejected(tmp_path: Path) -> None:
    tools = WorkspaceTools(tmp_path, timeout_seconds=1, max_output_bytes=100)
    with pytest.raises(ToolError, match="escapes"):
        tools.read("../outside.txt")


def test_tool_commands_use_a_minimal_environment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("RACEGREEN_TEST_SECRET", "must-not-leak")
    tools = WorkspaceTools(tmp_path, timeout_seconds=10, max_output_bytes=1000)

    output = tools.command(
        [
            sys.executable,
            "-c",
            "import os; print(os.environ.get('RACEGREEN_TEST_SECRET', ''))",
        ]
    )

    assert output.strip() == ""


def test_search_uses_a_minimal_environment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("RACEGREEN_TEST_SECRET", "must-not-leak")
    monkeypatch.setattr("racegreen.agent.tools.shutil.which", lambda _: "rg")
    captured: dict[str, object] = {}

    def fake_run(command, *, cwd, env, timeout_seconds, max_output_bytes):
        captured["command"] = command
        captured["env"] = env
        captured["cwd"] = cwd
        return BoundedCommandResult(0, "match\n", "", 0.0)

    monkeypatch.setattr("racegreen.agent.tools.run_bounded_command", fake_run)
    tools = WorkspaceTools(tmp_path, timeout_seconds=10, max_output_bytes=1000)

    assert tools.search("match") == "match\n"
    assert "RACEGREEN_TEST_SECRET" not in captured["env"]  # type: ignore[operator]
    assert captured["command"][1:5] == ["--no-heading", "--line-number", "--", "match"]
