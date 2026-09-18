from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from agent.context import ContextManager
from ai.provider import ModelRegistry
from ai.schemas import (
    ChatRequest,
    Message,
    ModelSpec,
    TextPart,
    UserMessage,
)


DEFAULT_BYTES_PER_TOKEN = 4
COMPACTION_SUMMARY_PREFIX = "[Summary of earlier conversation]"
SUMMARY_SYSTEM_PROMPT = (
    "Summarize the earlier coding-agent conversation. "
    "Preserve completed work, decisions, file paths, tool results, "
    "errors, and unresolved next steps. Be concise and factual."
)


class SummaryGenerator(Protocol):
    async def __call__(
        self,
        messages: Sequence[Message],
    ) -> str: ...

class SummaryGenerationError(RuntimeError):
    """Raised when a usable history summary cannot be produced."""


class ModelSummaryGenerator:
    def __init__(
        self,
        models: ModelRegistry,
        model: ModelSpec,
        *,
        system_prompt: str = SUMMARY_SYSTEM_PROMPT,
    ) -> None:
        self._models = models
        self._model = model
        self._system_prompt = system_prompt

    async def __call__(
        self,
        messages: Sequence[Message],
    ) -> str:
        request = ChatRequest(
            system_prompt=self._system_prompt,
            messages=list(messages),
            tools=[],
            temperature=0.0,
        )
        response = await self._models.complete(
            self._model,
            request,
        )

        if response.finish_reason == "length":
            raise SummaryGenerationError(
                "summary response was truncated"
            )

        summary = "".join(
            part.text
            for part in response.message.content
            if isinstance(part, TextPart)
        ).strip()

        if not summary:
            raise SummaryGenerationError(
                "model returned an empty summary"
            )

        return summary


@dataclass(frozen=True, slots=True)
class HistoryPartition:
    compactable: list[Message]
    retained: list[Message]


@dataclass(frozen=True, slots=True)
class CompactionResult:
    messages: list[Message]
    compacted_count: int

class CompactedContextManager(ContextManager):
    def __init__(
        self,
        *,
        summary_message: Message,
        compacted_count: int,
    ) -> None:
        if compacted_count <= 0:
            raise ValueError(
                "compacted_count must be positive"
            )

        super().__init__()
        self.summary_message = summary_message
        self.compacted_count = compacted_count

    def select(
        self,
        messages: Sequence[Message],
    ) -> list[Message]:
        if len(messages) < self.compacted_count:
            raise ValueError(
                "history is shorter than compacted_count"
            )

        return [
            self.summary_message,
            *messages[self.compacted_count :],
        ]

def partition_history(
    messages: Sequence[Message],
    *,
    keep_recent_turns: int,
) -> HistoryPartition:
    if keep_recent_turns <= 0:
        raise ValueError(
            "keep_recent_turns must be positive"
        )

    user_message_indexes = [
        index
        for index, message in enumerate(messages)
        if isinstance(message, UserMessage)
    ]

    if len(user_message_indexes) <= keep_recent_turns:
        return HistoryPartition(
            compactable=[],
            retained=list(messages),
        )

    split_index = user_message_indexes[-keep_recent_turns]

    return HistoryPartition(
        compactable=list(messages[:split_index]),
        retained=list(messages[split_index:]),
    )


def estimate_context_tokens(
    system_prompt: str,
    messages: Sequence[Message],
    *,
    bytes_per_token: int = DEFAULT_BYTES_PER_TOKEN,
) -> int:
    if bytes_per_token <= 0:
        raise ValueError(
            "bytes_per_token must be positive"
        )

    payload = {
        "system_prompt": system_prompt,
        "messages": [
            message.model_dump(
                mode="json",
                exclude={"timestamp"},
            )
            for message in messages
        ],
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    byte_count = len(serialized.encode("utf-8"))

    return max(
        1,
        (byte_count + bytes_per_token - 1)
        // bytes_per_token,
    )


def should_compact(
    system_prompt: str,
    messages: Sequence[Message],
    *,
    context_window: int | None,
    trigger_ratio: float = 0.8,
    bytes_per_token: int = DEFAULT_BYTES_PER_TOKEN,
) -> bool:
    if context_window is not None and context_window <= 0:
        raise ValueError(
            "context_window must be positive"
        )

    if not 0 < trigger_ratio <= 1:
        raise ValueError(
            "trigger_ratio must be between 0 and 1"
        )

    if context_window is None:
        return False

    estimated_tokens = estimate_context_tokens(
        system_prompt,
        messages,
        bytes_per_token=bytes_per_token,
    )
    trigger_tokens = (
        context_window * trigger_ratio
    )

    return estimated_tokens >= trigger_tokens


async def compact_history(
    messages: Sequence[Message],
    *,
    keep_recent_turns: int,
    summarizer: SummaryGenerator,
) -> CompactionResult:
    partition = partition_history(
        messages,
        keep_recent_turns=keep_recent_turns,
    )

    if not partition.compactable:
        return CompactionResult(
            messages=list(partition.retained),
            compacted_count=0,
        )

    summary = await summarizer(partition.compactable)
    summary_message = UserMessage(
        content=(
            f"{COMPACTION_SUMMARY_PREFIX}\n"
            f"{summary}"
        )
    )

    return CompactionResult(
        messages=[
            summary_message,
            *partition.retained,
        ],
        compacted_count=len(partition.compactable),
    )