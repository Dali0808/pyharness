from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from ai.schemas import Message, UserMessage


DEFAULT_BYTES_PER_TOKEN = 4


@dataclass(frozen=True, slots=True)
class HistoryPartition:
    compactable: list[Message]
    retained: list[Message]


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