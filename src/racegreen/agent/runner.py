from __future__ import annotations

import subprocess
import threading
import time
import uuid
from pathlib import Path

from .candidate import CandidateSnapshot, snapshot_candidate
from .events import EventStore
from .models import AgentContext, AgentStatus, ProviderResponse
from .provider import Provider, ProviderError
from .tools import ToolError, WorkspaceTools


class AgentRunner:
    """Runs one provider conversation inside one assigned worktree."""

    def __init__(self, provider: Provider, events: EventStore) -> None:
        self.provider = provider
        self.events = events
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def run(self, context: AgentContext, *, base_commit: str, candidate_dir: Path) -> AgentStatus:
        self.events.append(
            "agent_started",
            run_id=context.run_id,
            agent_id=context.agent_id,
            worktree=context.worktree,
            base_commit=base_commit,
        )
        messages: list[dict[str, str]] = [
            {"role": "user", "content": context.task_prompt},
        ]
        tools = WorkspaceTools(
            Path(context.worktree),
            timeout_seconds=context.limits.tool_timeout_seconds,
            max_output_bytes=context.limits.max_tool_output_bytes,
        )
        started = time.monotonic()
        self.events.append(
            "agent_status",
            run_id=context.run_id,
            agent_id=context.agent_id,
            status=AgentStatus.WORKING,
        )
        try:
            for _ in range(context.limits.max_tool_calls):
                if self._cancelled.is_set():
                    return self._finish(context, AgentStatus.CANCELLED)
                if time.monotonic() - started >= context.limits.timeout_seconds:
                    return self._finish(context, AgentStatus.TIMED_OUT)
                response = self.provider.complete(messages)
                self._record_response(context, response)
                if not response.tool_calls:
                    return self._finish(context, AgentStatus.COMPLETED)
                for call in response.tool_calls:
                    if call.name == "submit_candidate":
                        candidate = self._submit(
                            context, call.arguments, base_commit=base_commit, candidate_dir=candidate_dir
                        )
                        self.events.append(
                            "candidate_submitted",
                            run_id=context.run_id,
                            agent_id=context.agent_id,
                            candidate_id=candidate.candidate_id,
                            patch_hash=candidate.patch_hash,
                            patch_path=str(candidate.patch_path),
                        )
                        return self._finish(context, AgentStatus.SUBMITTED)
                    try:
                        output = tools.execute(call.name, call.arguments)
                    except ToolError as exc:
                        output = f"tool error: {exc}"
                    messages.append({"role": "tool", "content": output})
                    self.events.append(
                        "tool_completed",
                        run_id=context.run_id,
                        agent_id=context.agent_id,
                        tool=call.name,
                        output=output,
                    )
            return self._finish(context, AgentStatus.TIMED_OUT)
        except (ProviderError, ToolError, OSError, RuntimeError) as exc:
            self.events.append(
                "agent_failed",
                run_id=context.run_id,
                agent_id=context.agent_id,
                error=str(exc),
            )
            return self._finish(context, AgentStatus.PROVIDER_FAILED)

    def _submit(
        self, context: AgentContext, arguments: dict[str, object], *, base_commit: str, candidate_dir: Path
    ) -> CandidateSnapshot:
        candidate_id = str(arguments.get("candidate_id") or uuid.uuid4().hex)
        changed = self._changed_paths(Path(context.worktree), base_commit)
        if any(path == "tests" or path.startswith("tests/") for path in changed):
            raise ToolError("candidate modifies tests")
        return snapshot_candidate(
            Path(context.worktree),
            candidate_dir / f"{candidate_id}.patch",
            candidate_id=candidate_id,
            base_commit=base_commit,
        )

    @staticmethod
    def _changed_paths(worktree: Path, base_commit: str) -> tuple[str, ...]:
        result = subprocess.run(
            ["git", "-C", str(worktree), "diff", "--name-only", base_commit, "--"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise ToolError("unable to inspect candidate changes")
        return tuple(line for line in result.stdout.splitlines() if line)

    def _record_response(self, context: AgentContext, response: ProviderResponse) -> None:
        self.events.append(
            "provider_response",
            run_id=context.run_id,
            agent_id=context.agent_id,
            content=response.content,
            tool_calls=[call.name for call in response.tool_calls],
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            finish_reason=response.finish_reason,
        )

    def _finish(self, context: AgentContext, status: AgentStatus) -> AgentStatus:
        self.events.append(
            "agent_finished",
            run_id=context.run_id,
            agent_id=context.agent_id,
            status=status,
        )
        return status
