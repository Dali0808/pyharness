# src/pyharness/ai/types.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TextPart(Schema):
    type: Literal["text"] = "text"
    text: str


class ToolCallPart(Schema):
    type: Literal["tool_call"] = "tool_call"
    id: str
    name: str
    # 保留模型返回的原始 JSON，工具层再负责解析与校验。
    arguments_json: str


AssistantPart: TypeAlias = Annotated[
    TextPart | ToolCallPart,
    Field(discriminator="type"),
]


class UserMessage(Schema):
    role: Literal["user"] = "user"
    content: str
    timestamp: datetime = Field(default_factory=utc_now)


class AssistantMessage(Schema):
    role: Literal["assistant"] = "assistant"
    content: list[AssistantPart] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    response_id: str | None = None
    timestamp: datetime = Field(default_factory=utc_now)


class ToolResultMessage(Schema):
    role: Literal["tool_result"] = "tool_result"
    tool_call_id: str
    tool_name: str
    content: str
    is_error: bool = False
    timestamp: datetime = Field(default_factory=utc_now)


Message: TypeAlias = Annotated[
    UserMessage | AssistantMessage | ToolResultMessage,
    Field(discriminator="role"),
]


class ToolDefinition(Schema):
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema


class ModelSpec(Schema):
    provider: str
    id: str
    name: str | None = None
    context_window: int | None = None
    supports_tools: bool = True


class Usage(Schema):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_input_tokens: int = 0


class ChatRequest(Schema):
    system_prompt: str
    messages: list[Message]
    tools: list[ToolDefinition] = Field(default_factory=list)
    temperature: float | None = 0.0
    max_tokens: int | None = Field(default=None, gt=0)


class ChatResponse(Schema):
    message: AssistantMessage
    finish_reason: Literal["stop", "tool_calls", "length"]
    raw_finish_reason: str | None = None
    usage: Usage = Field(default_factory=Usage)