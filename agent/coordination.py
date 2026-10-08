from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from ai.schemas import Message, TextPart, Usage, UserMessage
from agent.agent import AgentState
from agent.events import EventHandler, SubtaskFinished, SubtaskStarted
from agent.loop import AgentRunner, RequestPreparer
from agent.tools import AgentTool


class DelegateArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    task: str = Field(min_length=1, max_length=2000)


def create_delegate_tool(
    runner: AgentRunner,
    make_state: Callable[[], AgentState],
    *,
    before_request: RequestPreparer | None = None,
    on_event: EventHandler | None = None,
    on_usage: Callable[[int, Usage], None] | None = None,
    on_history: Callable[[str, list[Message]], None] | None = None,
) -> AgentTool:
    async def delegate(arguments: BaseModel) -> str:
        assert isinstance(arguments, DelegateArgs)
        task_id = uuid4().hex
        if on_event is not None:
            on_event(SubtaskStarted(task_id=task_id, task=arguments.task))
        state = make_state()
        try:
            result = await runner.run(
                state,
                UserMessage(content=arguments.task),
                before_request=before_request,
            )
            if on_usage is not None:
                on_usage(result.steps, result.usage)
        finally:
            if on_history is not None:
                on_history(task_id, state.messages)
        if on_event is not None:
            on_event(SubtaskFinished(
                task_id=task_id,
                failure_code=result.failure.code if result.failure else None,
                steps=result.steps,
                usage=result.usage,
            ))
        if result.failure is not None:
            raise RuntimeError(f"subtask failed ({result.failure.code}): {result.failure.message}")
        assert result.final_message is not None
        answer = "".join(
            part.text for part in result.final_message.content
            if isinstance(part, TextPart)
        ).strip()
        if not answer:
            raise ValueError("subtask returned an empty answer")
        return answer if len(answer) <= 4000 else answer[:3969] + "\n[Subtask answer truncated]"

    return AgentTool.from_handler(
        name="delegate_task",
        description=(
            "Delegate a bounded, read-only investigation to a subagent. "
            "It can inspect workspace files but cannot edit files or run commands."
        ),
        args_model=DelegateArgs,
        handler=delegate,
    )
