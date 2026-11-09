from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from queue import Queue
from typing import Any

from ..verification.intake import CandidateIntake
from .candidate import CandidateSnapshot
from .events import EventStore
from .models import AgentContext, AgentStatus, ProviderResponse
from .provider import Provider, ProviderError, ProviderTimeout
from .tools import ToolError, WorkspaceTools


class AgentRunner:
    """Runs one provider conversation inside one assigned worktree."""

    def __init__(
        self,
        provider: Provider,
        events: EventStore,
        intake: CandidateIntake | None = None,
        is_terminal: bool | Callable[[], bool] = False,
        usage_callback: Callable[..., None] | None = None,
        rate_limiter: Any = None,
        rate_limit_timeout_seconds: float = 30.0,
    ) -> None:
        self.provider = provider
        self.events = events
        self.intake = intake or CandidateIntake(events=events)
        self.is_terminal = is_terminal
        self.usage_callback = usage_callback
        self.rate_limiter = rate_limiter
        self.rate_limit_timeout_seconds = rate_limit_timeout_seconds
        self.last_candidate: CandidateSnapshot | None = None
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def _terminal(self) -> bool:
        if callable(self.is_terminal):
            return bool(self.is_terminal())
        return bool(self.is_terminal) or self.events.is_terminal()

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
            {
                "role": "system",
                "content": (
                    "You are a coding agent working in an isolated git worktree. "
                    "Use the provided tools to inspect and modify files. "
                    "Prefer making targeted, working changes. "
                    "When the task is complete, you MUST call submit_candidate. "
                    "Do not paste full file contents in chat unless necessary; use the edit tool to write files. "
                    "If tool calls are supported, use them; otherwise respond with a JSON object "
                    '{"name":"<tool>","arguments":{...}}.'
                ),
            },
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
                if self._cancelled.is_set() or self._terminal():
                    return self._finish(context, AgentStatus.CANCELLED)
                remaining = context.limits.timeout_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    return self._finish(context, AgentStatus.TIMED_OUT)
                response = self._complete(messages, timeout_seconds=remaining)
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
                    if self._cancelled.is_set() or self._terminal():
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
                            is_terminal=self._terminal(),
                        )
                        if not intake_res.accepted or intake_res.snapshot is None:
                            self.events.append_atomic(
                                "candidate_rejected",
                                run_id=context.run_id,
                                agent_id=context.agent_id,
                                late_check=True,
                            )
                            return self._finish(context, AgentStatus.PROVIDER_FAILED)
                        self.last_candidate = intake_res.snapshot
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
        except ProviderTimeout:
            return self._finish(context, AgentStatus.TIMED_OUT)
        except (ProviderError, ToolError, OSError, RuntimeError) as exc:
            if type(exc).__name__ == "BudgetExceeded":
                raise
            self.events.append_atomic(
                "agent_failed",
                run_id=context.run_id,
                agent_id=context.agent_id,
                late_check=True,
                error=str(exc),
            )
            return self._finish(context, AgentStatus.PROVIDER_FAILED)

    def _complete(
        self, messages: list[dict[str, str]], *, timeout_seconds: float | None = None
    ) -> ProviderResponse | None:
        result: Queue[tuple[ProviderResponse | None, BaseException | None]] = Queue()
        call_cancelled = threading.Event()
        deadline = (
            None if timeout_seconds is None else time.monotonic() + timeout_seconds
        )

        def call_provider() -> None:
            try:
                if self.rate_limiter is not None:
                    if not self.rate_limiter.acquire(timeout=self.rate_limit_timeout_seconds):
                        raise ProviderError("provider rate limiter timeout")
                try:
                    resp = self.provider.complete(messages, cancel_event=call_cancelled)
                except TypeError:
                    resp = self.provider.complete(messages)
                result.put((resp, None))
            except (ProviderError, OSError, RuntimeError, ValueError) as exc:
                if self.rate_limiter is not None and isinstance(exc, ProviderError):
                    detail = str(exc).lower()
                    if "429" in detail or "rate" in detail:
                        self.rate_limiter.record_rate_limited()
                result.put((None, exc))
            except Exception as exc:  # noqa: BLE001 - never strand the caller
                # A provider adapter may raise an unexpected exception. Put it
                # on the queue so the waiting caller fails instead of blocking
                # forever on an empty queue.
                result.put((None, exc))

        thread = threading.Thread(target=call_provider, daemon=True)
        thread.start()
        while thread.is_alive():
            if self._cancelled.wait(0.05) or self._terminal():
                call_cancelled.set()
                return None
            if deadline is not None and time.monotonic() >= deadline:
                call_cancelled.set()
                raise ProviderTimeout("provider response timed out")
        response, error = result.get()
        if error is not None:
            raise error
        return response

    def _record_response(self, context: AgentContext, response: ProviderResponse) -> None:
        if self.usage_callback is not None:
            tokens = (response.input_tokens or 0) + (response.output_tokens or 0)
            self.usage_callback(
                context.agent_id,
                tokens=tokens,
                output_bytes=len(response.content.encode("utf-8")),
            )
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
