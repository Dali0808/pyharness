from __future__ import annotations

from dataclasses import dataclass, field

from ai.schemas import Message, ModelSpec
from agent.context import ContextManager
from agent.tools import ToolRegistry


@dataclass(slots=True)
class AgentState:
    system_prompt: str
    model: ModelSpec
    tools: ToolRegistry
    context_manager: ContextManager = field(default_factory=ContextManager)
    max_steps: int = 10
    messages: list[Message] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.max_steps <= 0:
            raise ValueError("max_steps must be positive")

    def add_message(self, message: Message) -> None:
        self.messages.append(message)

    def selected_messages(self) -> list[Message]:
        return self.context_manager.select(self.messages)
