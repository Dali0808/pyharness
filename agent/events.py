from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, TypeAlias

from ai.schemas import (
    AssistantMessage,
    ChatRequest,
    ChatResponse,
    ToolCallPart,
    ToolResultMessage,
)

FailureCode: TypeAlias = Literal[
    "max_steps_exceeded",
    "response_truncated",
    "provider_error",
]


@dataclass(frozen=True, slots=True)
class RunStarted:
    """Emitted after the user message is added to complete history."""


@dataclass(frozen=True, slots=True)
class ModelRequested:
    step: int
    request: ChatRequest


@dataclass(frozen=True, slots=True)
class ModelResponded:
    step: int
    response: ChatResponse


@dataclass(frozen=True, slots=True)
class ToolStarted:
    step: int
    call: ToolCallPart


@dataclass(frozen=True, slots=True)
class ToolFinished:
    step: int
    result: ToolResultMessage


@dataclass(frozen=True, slots=True)
class RunFinished:
    final_message: AssistantMessage
    steps: int


@dataclass(frozen=True, slots=True)
class RunFailed:
    code: FailureCode
    message: str
    steps: int


AgentEvent: TypeAlias = (
    RunStarted
    | ModelRequested
    | ModelResponded
    | ToolStarted
    | ToolFinished
    | RunFinished
    | RunFailed
)

EventHandler: TypeAlias = Callable[[AgentEvent], None]
