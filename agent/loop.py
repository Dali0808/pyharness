from __future__ import annotations

from dataclasses import dataclass

from ai.provider import ModelRegistry, ProviderError
from ai.schemas import (
    AssistantMessage,
    ChatRequest,
    ToolCallPart,
    Usage,
    UserMessage,
)
from agent.agent import AgentState
from agent.events import (
    AgentEvent,
    EventHandler,
    FailureCode,
    ModelRequested,
    ModelResponded,
    RunFailed,
    RunFinished,
    RunStarted,
    ToolFinished,
    ToolStarted,
)


@dataclass(frozen=True, slots=True)
class RunFailure:
    code: FailureCode
    message: str


@dataclass(frozen=True, slots=True)
class RunResult:
    final_message: AssistantMessage | None
    failure: RunFailure | None
    steps: int
    usage: Usage


class AgentRunner:
    def __init__(self, models: ModelRegistry) -> None:
        self._models = models

    async def run(
        self,
        state: AgentState,
        user_message: UserMessage,
        *,
        on_event: EventHandler | None = None,
    ) -> RunResult:
        state.add_message(user_message)
        self._emit(on_event, RunStarted())

        total_usage = Usage()
        steps = 0

        while steps < state.max_steps:
            steps += 1

            request = ChatRequest(
                system_prompt=state.system_prompt,
                messages=state.selected_messages(),
                tools=(
                    state.tools.definitions()
                    if state.model.supports_tools
                    else []
                ),
            )
            self._emit(
                on_event,
                ModelRequested(step=steps, request=request),
            )

            try:
                response = await self._models.complete(state.model, request)
            except ProviderError as exc:
                return self._fail(
                    on_event=on_event,
                    code="provider_error",
                    message=str(exc),
                    steps=steps,
                    usage=total_usage,
                )

            total_usage = self._add_usage(total_usage, response.usage)
            state.add_message(response.message)
            self._emit(
                on_event,
                ModelResponded(step=steps, response=response),
            )

            if response.finish_reason == "length":
                return self._fail(
                    on_event=on_event,
                    code="response_truncated",
                    message="Model response was truncated before completion.",
                    steps=steps,
                    usage=total_usage,
                )

            tool_calls = [
                part
                for part in response.message.content
                if isinstance(part, ToolCallPart)
            ]

            if not tool_calls:
                self._emit(
                    on_event,
                    RunFinished(
                        final_message=response.message,
                        steps=steps,
                    ),
                )
                return RunResult(
                    final_message=response.message,
                    failure=None,
                    steps=steps,
                    usage=total_usage,
                )

            for call in tool_calls:
                self._emit(
                    on_event,
                    ToolStarted(step=steps, call=call),
                )

                result = await state.tools.execute(call)
                state.add_message(result)

                self._emit(
                    on_event,
                    ToolFinished(step=steps, result=result),
                )

        return self._fail(
            on_event=on_event,
            code="max_steps_exceeded",
            message=(
                f"Agent reached max_steps={state.max_steps} "
                "without a final response."
            ),
            steps=steps,
            usage=total_usage,
        )

    def _fail(
        self,
        *,
        on_event: EventHandler | None,
        code: FailureCode,
        message: str,
        steps: int,
        usage: Usage,
    ) -> RunResult:
        failure = RunFailure(code=code, message=message)
        self._emit(
            on_event,
            RunFailed(
                code=failure.code,
                message=failure.message,
                steps=steps,
            ),
        )
        return RunResult(
            final_message=None,
            failure=failure,
            steps=steps,
            usage=usage,
        )

    @staticmethod
    def _add_usage(total: Usage, current: Usage) -> Usage:
        return Usage(
            input_tokens=total.input_tokens + current.input_tokens,
            output_tokens=total.output_tokens + current.output_tokens,
            total_tokens=total.total_tokens + current.total_tokens,
            cached_input_tokens=(
                total.cached_input_tokens + current.cached_input_tokens
            ),
        )

    @staticmethod
    def _emit(
        on_event: EventHandler | None,
        event: AgentEvent,
    ) -> None:
        if on_event is not None:
            on_event(event)
