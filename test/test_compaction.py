from __future__ import annotations

import pytest

from ai.schemas import (
    AssistantMessage,
    TextPart,
    ToolCallPart,
    ToolResultMessage,
    UserMessage,
)
from coding_agent.compaction import (
    estimate_context_tokens,
    partition_history,
    should_compact,
)


def assistant_text(text: str) -> AssistantMessage:
    return AssistantMessage(
        content=[TextPart(text=text)],
    )


def test_partition_keeps_latest_complete_user_turn(
) -> None:
    messages = [
        UserMessage(content="First task."),
        assistant_text("First answer."),
        UserMessage(content="Second task."),
        AssistantMessage(
            content=[
                ToolCallPart(
                    id="call-1",
                    name="read_file",
                    arguments_json='{"path":"task.txt"}',
                )
            ]
        ),
        ToolResultMessage(
            tool_call_id="call-1",
            tool_name="read_file",
            content="task contents",
        ),
        assistant_text("Second answer."),
    ]

    partition = partition_history(
        messages,
        keep_recent_turns=1,
    )

    assert partition.compactable == messages[:2]
    assert partition.retained == messages[2:]


def test_partition_does_not_modify_complete_history(
) -> None:
    messages = [
        UserMessage(content="First task."),
        assistant_text("First answer."),
        UserMessage(content="Second task."),
        assistant_text("Second answer."),
    ]
    original = list(messages)

    partition = partition_history(
        messages,
        keep_recent_turns=1,
    )

    assert messages == original
    assert partition.compactable is not messages
    assert partition.retained is not messages


def test_partition_keeps_everything_when_history_is_short(
) -> None:
    messages = [
        UserMessage(content="Only task."),
        assistant_text("Only answer."),
    ]

    partition = partition_history(
        messages,
        keep_recent_turns=2,
    )

    assert partition.compactable == []
    assert partition.retained == messages
    assert partition.retained is not messages


@pytest.mark.parametrize("keep_recent_turns", [0, -1])
def test_partition_rejects_non_positive_retention(
    keep_recent_turns: int,
) -> None:
    with pytest.raises(
        ValueError,
        match="keep_recent_turns must be positive",
    ):
        partition_history(
            [],
            keep_recent_turns=keep_recent_turns,
        )


def test_estimate_context_tokens_grows_with_content(
) -> None:
    short_messages = [
        UserMessage(content="short"),
    ]
    long_messages = [
        UserMessage(content="x" * 400),
    ]

    short_estimate = estimate_context_tokens(
        "System prompt.",
        short_messages,
    )
    long_estimate = estimate_context_tokens(
        "System prompt.",
        long_messages,
    )

    assert short_estimate > 0
    assert long_estimate > short_estimate


def test_estimate_context_tokens_includes_structured_tool_data(
) -> None:
    plain_messages = [
        UserMessage(content="Run a tool."),
    ]
    tool_messages = [
        UserMessage(content="Run a tool."),
        AssistantMessage(
            content=[
                ToolCallPart(
                    id="call-1",
                    name="read_file",
                    arguments_json=(
                        '{"path":"a/very/long/path/to/task.txt"}'
                    ),
                )
            ]
        ),
        ToolResultMessage(
            tool_call_id="call-1",
            tool_name="read_file",
            content="x" * 200,
        ),
    ]

    plain_estimate = estimate_context_tokens(
        "",
        plain_messages,
    )
    tool_estimate = estimate_context_tokens(
        "",
        tool_messages,
    )

    assert tool_estimate > plain_estimate


def test_should_compact_at_configured_threshold(
) -> None:
    messages = [
        UserMessage(content="x" * 200),
    ]
    estimated_tokens = estimate_context_tokens(
        "System prompt.",
        messages,
    )

    assert should_compact(
        "System prompt.",
        messages,
        context_window=estimated_tokens,
        trigger_ratio=1.0,
    )
    assert not should_compact(
        "System prompt.",
        messages,
        context_window=estimated_tokens + 1,
        trigger_ratio=1.0,
    )


def test_should_not_compact_without_context_window(
) -> None:
    messages = [
        UserMessage(content="x" * 1000),
    ]

    assert not should_compact(
        "System prompt.",
        messages,
        context_window=None,
    )


@pytest.mark.parametrize(
    ("context_window", "trigger_ratio", "error_message"),
    [
        (0, 0.8, "context_window must be positive"),
        (-1, 0.8, "context_window must be positive"),
        (100, 0.0, "trigger_ratio must be between 0 and 1"),
        (100, -0.1, "trigger_ratio must be between 0 and 1"),
        (100, 1.1, "trigger_ratio must be between 0 and 1"),
    ],
)
def test_should_compact_rejects_invalid_configuration(
    context_window: int,
    trigger_ratio: float,
    error_message: str,
) -> None:
    with pytest.raises(ValueError, match=error_message):
        should_compact(
            "",
            [],
            context_window=context_window,
            trigger_ratio=trigger_ratio,
        )