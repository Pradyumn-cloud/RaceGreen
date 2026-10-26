from __future__ import annotations

import os
import subprocess
import threading
import time
import uuid
from pathlib import Path
from queue import Queue

from ..verification.intake import CandidateIntake
from .candidate import CandidateSnapshot, snapshot_candidate, verify_candidate_snapshot
from .events import EventStore
from .models import AgentContext, AgentStatus, ProviderResponse
from .provider import Provider, ProviderError
from .tools import ToolError, WorkspaceTools


class AgentRunner:
    """Runs one provider conversation inside one assigned worktree."""

    def __init__(self, provider: Provider, events: EventStore, intake: CandidateIntake | None = None, is_terminal: bool = False) -> None:
        self.provider = provider
        self.events = events
        self.intake = intake or CandidateIntake(events=events)
        self.is_terminal = is_terminal
        self._cancelled = threading.Event()
        # Set env for agent
        os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

    def cancel(self) -> None:
        self._cancelled.set()

    def run(self, context: AgentContext, *, base_commit: str, candidate_dir: Path) -> AgentStatus:
        result = self.events.append_atomic(
            "agent_started",
            run_id=context.run_id,
            agent_id=context.agent_id,
            late_check=True,
            worktree=context.worktree,
            base_commit=base_commit,
        )
        if result is None:
            return AgentStatus.CANCELLED
        messages: list[dict[str, str]] = [
            {"role": "user", "content": context.task_prompt},
        ]
        tools = WorkspaceTools(
            Path(context.worktree),
            timeout_seconds=context.limits.tool_timeout_seconds,
            max_output_bytes=context.limits.max_tool_output_bytes,
            cancel_event=self._cancelled,
        )
        started = time.monotonic()
        res = self.events.append_atomic(
            "agent_status",
            run_id=context.run_id,
            agent_id=context.agent_id,
            late_check=True,
            status=AgentStatus.WORKING,
        )
        if res is None:
            return self._finish(context, AgentStatus.CANCELLED)
        try:
            for _ in range(context.limits.max_tool_calls):
                if self._cancelled.is_set():
                    return self._finish(context, AgentStatus.CANCELLED)
                if time.monotonic() - started >= context.limits.timeout_seconds:
                    return self._finish(context, AgentStatus.TIMED_OUT)
                response = self._complete(messages)
                if response is None:
                    self.events.append_atomic(
                        "late_response_rejected",
                        run_id=context.run_id,
                        agent_id=context.agent_id,
                        late_check=True,
                    )
                    return self._finish(context, AgentStatus.CANCELLED)
                self._record_response(context, response)
                if not response.tool_calls:
                    return self._finish(context, AgentStatus.COMPLETED)
                for call in response.tool_calls:
                    if self._cancelled.is_set():
                        return self._finish(context, AgentStatus.CANCELLED)
                    if call.name == "submit_candidate":
                        # wait for child processes to exit (best effort)
                        time.sleep(0.1)
                        intake_res = self.intake.freeze_and_intake(
                            Path(context.worktree),
                            candidate_dir,
                            str(call.arguments.get("candidate_id") or uuid.uuid4().hex),
                            base_commit,
                            context.run_id,
                            context.agent_id,
                            is_terminal=self.events.is_terminal() or self.is_terminal,
                        )
                        if not intake_res.accepted or intake_res.snapshot is None:
                            self.events.append_atomic(
                                "candidate_rejected",
                                run_id=context.run_id,
                                agent_id=context.agent_id,
                                late_check=True,
                            )
                            return self._finish(context, AgentStatus.PROVIDER_FAILED)
                        self.events.append_atomic(
                            "candidate_submitted",
                            run_id=context.run_id,
                            agent_id=context.agent_id,
                            late_check=True,
                            candidate_id=intake_res.snapshot.candidate_id,
                            patch_hash=intake_res.snapshot.patch_hash,
                            patch_path=str(intake_res.snapshot.patch_path),
                        )
                        return self._finish(context, AgentStatus.SUBMITTED)
                    try:
                        output = tools.execute(call.name, call.arguments)
                    except ToolError as exc:
                        output = f"tool error: {exc}"
                    messages.append({"role": "tool", "content": output})
                    self.events.append_atomic(
                        "tool_completed",
                        run_id=context.run_id,
                        agent_id=context.agent_id,
                        late_check=True,
                        tool=call.name,
                        output=output,
                    )
            return self._finish(context, AgentStatus.TIMED_OUT)
        except (ProviderError, ToolError, OSError, RuntimeError) as exc:
            self.events.append_atomic(
                "agent_failed",
                run_id=context.run_id,
                agent_id=context.agent_id,
                late_check=True,
                error=str(exc),
            )
            return self._finish(context, AgentStatus.PROVIDER_FAILED)

    def _complete(self, messages: list[dict[str, str]]) -> ProviderResponse | None:
        result: Queue[tuple[ProviderResponse | None, BaseException | None]] = Queue(maxsize=1)

        def call_provider() -> None:
            try:
                try:
                    resp = self.provider.complete(messages, cancel_event=self._cancelled)  # type: ignore
                except TypeError:
                    resp = self.provider.complete(messages)
                result.put((resp, None))
            except (ProviderError, OSError, RuntimeError, ValueError) as exc:
                result.put((None, exc))

        thread = threading.Thread(target=call_provider, daemon=True)
        thread.start()
        while thread.is_alive():
            if self._cancelled.wait(0.05):
                return None
        response, error = result.get()
        if error is not None:
            raise error
        return response

    def _submit(
        self, context: AgentContext, arguments: dict[str, object], *, base_commit: str, candidate_dir: Path
    ) -> CandidateSnapshot:
        candidate_id = str(arguments.get("candidate_id") or uuid.uuid4().hex)
        changed = self._changed_paths(Path(context.worktree), base_commit)
        if any(path == "tests" or path.startswith("tests/") for path in changed):
            raise ToolError("candidate modifies tests")
        candidate = snapshot_candidate(
            Path(context.worktree),
            candidate_dir / f"{candidate_id}.patch",
            candidate_id=candidate_id,
            base_commit=base_commit,
        )
        verify_candidate_snapshot(Path(context.worktree), candidate)
        return candidate

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
        self.events.append_atomic(
            "provider_response",
            run_id=context.run_id,
            agent_id=context.agent_id,
            late_check=True,
            content=response.content,
            tool_calls=[call.name for call in response.tool_calls],
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            finish_reason=response.finish_reason,
        )

    def _finish(self, context: AgentContext, status: AgentStatus) -> AgentStatus:
        self.events.append_atomic(
            "agent_finished",
            run_id=context.run_id,
            agent_id=context.agent_id,
            late_check=True,
            status=status,
        )
        return status
