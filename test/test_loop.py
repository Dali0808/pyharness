from __future__ import annotations

import pytest

from agent.agent import AgentState
from agent.events import (
    ModelRequested,
    ModelResponded,
    RunFailed,
    RunFinished,
    RunStarted,
    ToolFinished,
    ToolStarted,
)
from agent.loop import AgentRunner
from agent.tools import ToolRegistry, create_calculator_tool
from ai.provider import ModelRegistry, ProviderError, ScriptedProvider
from ai.schemas import (
    AssistantMessage,
    ChatRequest,
    ChatResponse,
    ModelSpec,
    TextPart,
    ToolCallPart,
    ToolResultMessage,
    Usage,
    UserMessage,
)


def make_model(
    *,
    provider: str = "scripted",
) -> ModelSpec:
    return ModelSpec(provider=provider, id="test-model")


def make_state(
    model: ModelSpec,
    *,
    max_steps: int = 3,
) -> AgentState:
    tools = ToolRegistry()
    tools.register(create_calculator_tool())

    return AgentState(
        system_prompt="You are a helpful assistant.",
        model=model,
        tools=tools,
        max_steps=max_steps,
    )


def make_runner(
    model: ModelSpec,
    responses: list[ChatResponse],
) -> tuple[AgentRunner, ScriptedProvider]:
    provider = ScriptedProvider(responses)
    models = ModelRegistry()
    models.register(model, provider)

    return AgentRunner(models), provider


@pytest.mark.asyncio
async def test_runner_completes_tool_call_cycle_and_records_history() -> None:
    model = make_model()
    runner, provider = make_runner(
        model,
        [
            ChatResponse(
                message=AssistantMessage(
                    content=[
                        ToolCallPart(
                            id="call-1",
                            name="calculator",
                            arguments_json='{"expression":"21 * 2"}',
                        )
                    ]
                ),
                finish_reason="tool_calls",
                usage=Usage(
                    input_tokens=10,
                    output_tokens=2,
                    total_tokens=12,
                    cached_input_tokens=1,
                ),
            ),
            ChatResponse(
                message=AssistantMessage(
                    content=[TextPart(text="The answer is 42.")]
                ),
                finish_reason="stop",
                usage=Usage(
                    input_tokens=8,
                    output_tokens=3,
                    total_tokens=11,
                    cached_input_tokens=2,
                ),
            ),
        ],
    )
    state = make_state(model)
    events = []

    result = await runner.run(
        state,
        UserMessage(content="What is 21 * 2?"),
        on_event=events.append,
    )

    assert result.failure is None
    assert result.final_message is not None
    assert result.steps == 2
    assert result.usage.input_tokens == 18
    assert result.usage.output_tokens == 5
    assert result.usage.total_tokens == 23
    assert result.usage.cached_input_tokens == 3

    final_part = result.final_message.content[0]
    assert isinstance(final_part, TextPart)
    assert final_part.text == "The answer is 42."

    assert [message.role for message in state.messages] == [
        "user",
        "assistant",
        "tool_result",
        "assistant",
    ]

    tool_result = state.messages[2]
    assert isinstance(tool_result, ToolResultMessage)
    assert tool_result.content == "42"
    assert tool_result.is_error is False

    assert len(provider.requests) == 2
    assert [message.role for message in provider.requests[0].messages] == [
        "user",
    ]
    assert [message.role for message in provider.requests[1].messages] == [
        "user",
        "assistant",
        "tool_result",
    ]
    assert provider.requests[0].tools[0].name == "calculator"

    assert [type(event) for event in events] == [
        RunStarted,
        ModelRequested,
        ModelResponded,
        ToolStarted,
        ToolFinished,
        ModelRequested,
        ModelResponded,
        RunFinished,
    ]


@pytest.mark.asyncio
async def test_tool_error_is_returned_to_model_and_run_can_recover() -> None:
    model = make_model()
    runner, provider = make_runner(
        model,
        [
            ChatResponse(
                message=AssistantMessage(
                    content=[
                        ToolCallPart(
                            id="call-missing",
                            name="missing_tool",
                            arguments_json="{}",
                        )
                    ]
                ),
                finish_reason="tool_calls",
            ),
            ChatResponse(
                message=AssistantMessage(
                    content=[TextPart(text="I corrected the tool call.")]
                ),
                finish_reason="stop",
            ),
        ],
    )
    state = make_state(model)

    result = await runner.run(
        state,
        UserMessage(content="Use a tool."),
    )

    assert result.failure is None
    assert result.steps == 2

    tool_result = state.messages[2]
    assert isinstance(tool_result, ToolResultMessage)
    assert tool_result.is_error is True
    assert tool_result.content.startswith("unknown_tool:")

    second_request_result = provider.requests[1].messages[-1]
    assert isinstance(second_request_result, ToolResultMessage)
    assert second_request_result.is_error is True


@pytest.mark.asyncio
async def test_runner_fails_after_reaching_max_steps() -> None:
    model = make_model()
    runner, provider = make_runner(
        model,
        [
            ChatResponse(
                message=AssistantMessage(
                    content=[
                        ToolCallPart(
                            id="call-1",
                            name="calculator",
                            arguments_json='{"expression":"1 + 1"}',
                        )
                    ]
                ),
                finish_reason="tool_calls",
            )
        ],
    )
    state = make_state(model, max_steps=1)

    result = await runner.run(
        state,
        UserMessage(content="Calculate 1 + 1."),
    )

    assert result.final_message is None
    assert result.failure is not None
    assert result.failure.code == "max_steps_exceeded"
    assert result.steps == 1
    assert len(provider.requests) == 1
    assert [message.role for message in state.messages] == [
        "user",
        "assistant",
        "tool_result",
    ]


@pytest.mark.asyncio
async def test_runner_fails_when_model_response_is_truncated() -> None:
    model = make_model()
    runner, _ = make_runner(
        model,
        [
            ChatResponse(
                message=AssistantMessage(
                    content=[TextPart(text="Partial response")]
                ),
                finish_reason="length",
            )
        ],
    )
    state = make_state(model)
    events = []

    result = await runner.run(
        state,
        UserMessage(content="Write a long response."),
        on_event=events.append,
    )

    assert result.final_message is None
    assert result.failure is not None
    assert result.failure.code == "response_truncated"
    assert result.steps == 1
    assert [message.role for message in state.messages] == [
        "user",
        "assistant",
    ]
    assert [type(event) for event in events] == [
        RunStarted,
        ModelRequested,
        ModelResponded,
        RunFailed,
    ]


class FailingProvider:
    id = "failing"

    async def complete(
        self,
        model: ModelSpec,
        request: ChatRequest,
    ) -> ChatResponse:
        raise ProviderError("network unavailable")


@pytest.mark.asyncio
async def test_runner_preserves_history_when_provider_fails() -> None:
    model = make_model(provider="failing")
    models = ModelRegistry()
    models.register(model, FailingProvider())

    state = make_state(model)
    events = []

    result = await AgentRunner(models).run(
        state,
        UserMessage(content="Hello"),
        on_event=events.append,
    )

    assert result.final_message is None
    assert result.failure is not None
    assert result.failure.code == "provider_error"
    assert result.failure.message == "network unavailable"
    assert result.steps == 1
    assert [message.role for message in state.messages] == ["user"]
    assert [type(event) for event in events] == [
        RunStarted,
        ModelRequested,
        RunFailed,
    ]
