from __future__ import annotations

import threading
from pathlib import Path

import pytest

from racegreen.agent.tools import ToolError, WorkspaceTools


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
