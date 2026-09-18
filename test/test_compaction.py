from __future__ import annotations

from collections.abc import Sequence

import pytest

from ai.provider import ModelRegistry, ProviderError, ScriptedProvider
from ai.schemas import (
    AssistantMessage,
    Message,
    TextPart,
    ToolCallPart,
    ToolResultMessage,
    UserMessage,
    ChatResponse,
    ModelSpec,
)
from coding_agent.compaction import (
    COMPACTION_SUMMARY_PREFIX,
    compact_history,
    estimate_context_tokens,
    partition_history,
    should_compact,
    ModelSummaryGenerator,
    SUMMARY_SYSTEM_PROMPT,
    SummaryGenerationError,
    CompactedContextManager,
)

class RecordingSummarizer:
    def __init__(
        self,
        summary: str = "Earlier work was summarized.",
    ) -> None:
        self.summary = summary
        self.calls: list[list[Message]] = []

    async def __call__(
        self,
        messages: Sequence[Message],
    ) -> str:
        self.calls.append(list(messages))
        return self.summary


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


@pytest.mark.asyncio
async def test_compact_history_replaces_early_turns_with_summary(
) -> None:
    messages = [
        UserMessage(content="First task."),
        assistant_text("First answer."),
        UserMessage(content="Second task."),
        assistant_text("Second answer."),
    ]
    original = list(messages)
    summarizer = RecordingSummarizer(
        "The first task was completed.",
    )

    result = await compact_history(
        messages,
        keep_recent_turns=1,
        summarizer=summarizer,
    )

    assert summarizer.calls == [messages[:2]]
    assert result.compacted_count == 2
    assert len(result.messages) == 3

    summary_message = result.messages[0]
    assert isinstance(summary_message, UserMessage)
    assert summary_message.content == (
        f"{COMPACTION_SUMMARY_PREFIX}\n"
        "The first task was completed."
    )

    assert result.messages[1:] == messages[2:]
    assert messages == original
    assert result.messages is not messages


@pytest.mark.asyncio
async def test_compact_history_skips_summarizer_when_nothing_is_compactable(
) -> None:
    messages = [
        UserMessage(content="Only task."),
        assistant_text("Only answer."),
    ]
    summarizer = RecordingSummarizer()

    result = await compact_history(
        messages,
        keep_recent_turns=2,
        summarizer=summarizer,
    )

    assert summarizer.calls == []
    assert result.compacted_count == 0
    assert result.messages == messages
    assert result.messages is not messages


@pytest.mark.asyncio
async def test_compact_history_preserves_original_when_summarizer_fails(
) -> None:
    messages = [
        UserMessage(content="First task."),
        assistant_text("First answer."),
        UserMessage(content="Second task."),
        assistant_text("Second answer."),
    ]
    original = list(messages)

    async def failing_summarizer(
        compactable: Sequence[Message],
    ) -> str:
        raise RuntimeError("summary unavailable")

    with pytest.raises(
        RuntimeError,
        match="summary unavailable",
    ):
        await compact_history(
            messages,
            keep_recent_turns=1,
            summarizer=failing_summarizer,
        )

    assert messages == original


def make_summary_generator(
    responses: list[ChatResponse],
) -> tuple[ModelSummaryGenerator, ScriptedProvider]:
    model = ModelSpec(
        provider="scripted",
        id="summary-model",
    )
    provider = ScriptedProvider(responses)
    models = ModelRegistry()
    models.register(model, provider)

    return ModelSummaryGenerator(models, model), provider


@pytest.mark.asyncio
async def test_model_summarizer_requests_summary_without_tools(
) -> None:
    messages = [
        UserMessage(content="Inspect the project."),
        assistant_text("The project uses Python."),
    ]
    summarizer, provider = make_summary_generator(
        [
            ChatResponse(
                message=AssistantMessage(
                    content=[
                        TextPart(text="Project inspected. "),
                        TextPart(text="It uses Python."),
                    ]
                ),
                finish_reason="stop",
            )
        ]
    )

    summary = await summarizer(messages)

    assert summary == "Project inspected. It uses Python."
    assert len(provider.requests) == 1

    request = provider.requests[0]
    assert request.system_prompt == SUMMARY_SYSTEM_PROMPT
    assert request.messages == messages
    assert request.tools == []
    assert request.temperature == 0.0


@pytest.mark.asyncio
async def test_model_summarizer_rejects_empty_summary(
) -> None:
    summarizer, _ = make_summary_generator(
        [
            ChatResponse(
                message=AssistantMessage(content=[]),
                finish_reason="stop",
            )
        ]
    )

    with pytest.raises(
        SummaryGenerationError,
        match="empty summary",
    ):
        await summarizer(
            [UserMessage(content="Old task.")]
        )


@pytest.mark.asyncio
async def test_model_summarizer_rejects_truncated_summary(
) -> None:
    summarizer, _ = make_summary_generator(
        [
            ChatResponse(
                message=AssistantMessage(
                    content=[TextPart(text="Partial summary")]
                ),
                finish_reason="length",
            )
        ]
    )

    with pytest.raises(
        SummaryGenerationError,
        match="summary response was truncated",
    ):
        await summarizer(
            [UserMessage(content="Old task.")]
        )


@pytest.mark.asyncio
async def test_model_summarizer_propagates_provider_error(
) -> None:
    summarizer, _ = make_summary_generator([])

    with pytest.raises(
        ProviderError,
        match="no remaining response",
    ):
        await summarizer(
            [UserMessage(content="Old task.")]
        )


def test_compacted_context_selects_summary_and_uncompacted_tail(
) -> None:
    messages = [
        UserMessage(content="First task."),
        assistant_text("First answer."),
        UserMessage(content="Second task."),
        assistant_text("Second answer."),
    ]
    original = list(messages)
    summary_message = UserMessage(
        content=(
            f"{COMPACTION_SUMMARY_PREFIX}\n"
            "The first task was completed."
        )
    )
    manager = CompactedContextManager(
        summary_message=summary_message,
        compacted_count=2,
    )

    selected = manager.select(messages)

    assert selected == [
        summary_message,
        *messages[2:],
    ]
    assert messages == original
    assert selected is not messages


def test_compacted_context_includes_newly_appended_messages(
) -> None:
    messages = [
        UserMessage(content="First task."),
        assistant_text("First answer."),
        UserMessage(content="Second task."),
        assistant_text("Second answer."),
    ]
    summary_message = UserMessage(
        content=(
            f"{COMPACTION_SUMMARY_PREFIX}\n"
            "The first task was completed."
        )
    )
    manager = CompactedContextManager(
        summary_message=summary_message,
        compacted_count=2,
    )

    new_message = UserMessage(content="Third task.")
    messages.append(new_message)

    assert manager.select(messages) == [
        summary_message,
        *messages[2:],
    ]
    assert manager.select(messages)[-1] == new_message


@pytest.mark.parametrize("compacted_count", [0, -1])
def test_compacted_context_rejects_non_positive_count(
    compacted_count: int,
) -> None:
    with pytest.raises(
        ValueError,
        match="compacted_count must be positive",
    ):
        CompactedContextManager(
            summary_message=UserMessage(
                content="Summary"
            ),
            compacted_count=compacted_count,
        )


def test_compacted_context_rejects_shorter_complete_history(
) -> None:
    manager = CompactedContextManager(
        summary_message=UserMessage(content="Summary"),
        compacted_count=3,
    )

    with pytest.raises(
        ValueError,
        match="history is shorter than compacted_count",
    ):
        manager.select(
            [UserMessage(content="Only message.")]
        )