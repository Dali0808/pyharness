from __future__ import annotations

import pytest

from agent.agent import AgentState
from agent.context import ContextManager
from agent.tools import ToolRegistry
from ai.schemas import Message, ModelSpec, UserMessage


def make_messages(*contents: str) -> list[Message]:
    return [UserMessage(content=content) for content in contents]


def message_contents(messages: list[Message]) -> list[str]:
    return [message.content for message in messages]


def make_state(
    *,
    context_manager: ContextManager | None = None,
    max_steps: int = 10,
) -> AgentState:
    return AgentState(
        system_prompt="You are helpful.",
        model=ModelSpec(provider="scripted", id="test-model"),
        tools=ToolRegistry(),
        context_manager=context_manager or ContextManager(),
        max_steps=max_steps,
    )


def test_context_selects_recent_messages_without_mutating_history() -> None:
    history = make_messages("one", "two", "three", "four")
    manager = ContextManager(max_messages=2)

    selected = manager.select(history)

    assert message_contents(selected) == ["three", "four"]
    assert message_contents(history) == ["one", "two", "three", "four"]

    selected.pop()

    assert message_contents(history) == ["one", "two", "three", "four"]


def test_unbounded_context_returns_all_messages_in_a_new_list() -> None:
    history = make_messages("one", "two")
    manager = ContextManager()

    selected = manager.select(history)

    assert message_contents(selected) == ["one", "two"]
    assert selected is not history


@pytest.mark.parametrize("max_messages", [0, -1])
def test_context_manager_rejects_non_positive_limits(
    max_messages: int,
) -> None:
    with pytest.raises(ValueError, match="max_messages must be positive"):
        ContextManager(max_messages=max_messages)


def test_agent_state_keeps_full_history_while_context_is_trimmed() -> None:
    state = make_state(context_manager=ContextManager(max_messages=2))

    for message in make_messages("one", "two", "three"):
        state.add_message(message)

    assert message_contents(state.messages) == ["one", "two", "three"]
    assert message_contents(state.selected_messages()) == ["two", "three"]
    assert state.max_steps == 10
    assert state.model.id == "test-model"


@pytest.mark.parametrize("max_steps", [0, -1])
def test_agent_state_rejects_non_positive_max_steps(
    max_steps: int,
) -> None:
    with pytest.raises(ValueError, match="max_steps must be positive"):
        make_state(max_steps=max_steps)