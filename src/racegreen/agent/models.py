from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class AgentStatus(StrEnum):
    STARTED = "started"
    WORKING = "working"
    SUBMITTED = "submitted"
    COMPLETED = "completed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    PROVIDER_FAILED = "provider_failed"


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ProviderResponse:
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    input_tokens: int | None = None
    output_tokens: int | None = None
    finish_reason: str | None = None


@dataclass(frozen=True)
class AgentLimits:
    timeout_seconds: float = 300.0
    tool_timeout_seconds: float = 30.0
    max_tool_output_bytes: int = 32_000
    max_tool_calls: int = 100


@dataclass
class AgentContext:
    run_id: str
    agent_id: str
    worktree: str
    task_prompt: str
    limits: AgentLimits = field(default_factory=AgentLimits)
