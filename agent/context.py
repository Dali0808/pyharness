from __future__ import annotations

from collections.abc import Sequence

from ai.schemas import Message


class ContextManager:
    def __init__(self, *, max_messages: int | None = None) -> None:
        if max_messages is not None and max_messages <= 0:
            raise ValueError("max_messages must be positive or None")

        self.max_messages = max_messages

    def select(self, messages: Sequence[Message]) -> list[Message]:
        if self.max_messages is None:
            return list(messages)

        return list(messages[-self.max_messages :])