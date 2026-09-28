from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from agent.context import ContextManager
from agent.agent import AgentState
from agent.loop import RequestPreparationError
from ai.provider import ModelRegistry
from ai.schemas import (
    ChatRequest,
    Message,
    ModelSpec,
    TextPart,
    ToolDefinition,
    ToolResultMessage,
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
        max_tokens: int | None = None,
    ) -> None:
        self._models = models
        self._model = model
        self._system_prompt = system_prompt
        self._max_tokens = max_tokens

    async def __call__(
        self,
        messages: Sequence[Message],
    ) -> str:
        request = ChatRequest(
            system_prompt=self._system_prompt,
            messages=list(messages),
            tools=[],
            temperature=0.0,
            max_tokens=self._max_tokens,
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
    tools: Sequence[ToolDefinition] = (),
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
        "tools": [tool.model_dump(mode="json") for tool in tools],
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


class RequestBudgetManager(ContextManager):
    """Build a bounded request view without changing complete history."""

    def __init__(
        self,
        *,
        context_window: int,
        summarizer: SummaryGenerator,
        on_compaction: Callable[[int], None] | None = None,
    ) -> None:
        super().__init__()
        if context_window <= 0:
            raise ValueError("context_window must be positive")
        self.context_window = context_window
        self.output_reserve = max(32, min(1024, context_window // 8))
        self._summarizer = summarizer
        self._on_compaction = on_compaction
        self._summary: UserMessage | None = None
        self._compacted_count = 0
        self._tool_result_limit: int | None = None

    def select(self, messages: Sequence[Message]) -> list[Message]:
        return self._view(
            messages,
            self._summary,
            self._compacted_count,
            self._tool_result_limit,
        )

    async def prepare(self, state: AgentState) -> None:
        # Keep enough headroom for the larger workspace-tool declaration set.
        input_budget = int(self.context_window * 0.86) - self.output_reserve
        if input_budget <= 0:
            raise RequestPreparationError(
                "context_budget_exceeded",
                "context window leaves no room for an output token reserve",
            )

        tools = state.tools.definitions() if state.model.supports_tools else []
        summary = self._summary
        compacted_count = self._compacted_count
        tool_result_limit = self._tool_result_limit
        compacted_now = 0

        for _ in range(12):
            view = self._view(
                state.messages, summary, compacted_count, tool_result_limit
            )
            estimate = estimate_context_tokens(
                state.system_prompt,
                view,
                tools=tools,
                bytes_per_token=2,
            )
            if estimate <= input_budget:
                self._summary = summary
                self._compacted_count = compacted_count
                self._tool_result_limit = tool_result_limit
                if compacted_now and self._on_compaction is not None:
                    self._on_compaction(compacted_now)
                return

            partition = partition_history(view, keep_recent_turns=2)
            if not partition.compactable:
                partition = partition_history(view, keep_recent_turns=1)
            if partition.compactable:
                try:
                    summary_text = await self._summarize_bounded(
                        partition.compactable
                    )
                except RequestPreparationError:
                    raise
                except Exception as exc:
                    raise RequestPreparationError(
                        "compaction_failed", f"summary generation failed: {exc}"
                    ) from exc
                was_summarized = summary is not None
                summary = UserMessage(
                    content=f"{COMPACTION_SUMMARY_PREFIX}\n{summary_text}"
                )
                dropped = len(partition.compactable) - int(was_summarized)
                compacted_count += dropped
                compacted_now += dropped
                continue

            can_reduce_tool_results = (
                tool_result_limit is None or tool_result_limit > 128
            ) and any(
                isinstance(message, ToolResultMessage)
                and len(message.content) > 128
                for message in view
            )
            if can_reduce_tool_results:
                tool_result_limit = (
                    min(2048, self.context_window)
                    if tool_result_limit is None
                    else max(128, tool_result_limit // 2)
                )
                continue
            break

        raise RequestPreparationError(
            "context_budget_exceeded",
            "request exceeds the context budget after safe compaction",
        )

    def _limit_summary_input(self, messages: Sequence[Message]) -> list[Message]:
        return self._view(
            messages,
            None,
            0,
            min(256, max(64, self.context_window // 8)),
        )

    async def _summarize_bounded(self, messages: Sequence[Message]) -> str:
        bounded = self._limit_summary_input(messages)
        turns: list[list[Message]] = []
        for message in bounded:
            if isinstance(message, UserMessage) or not turns:
                turns.append([])
            turns[-1].append(message)

        limit = max(256, self.context_window // 2)
        carry: list[Message] = []
        for turn in turns:
            candidate = [*carry, *turn]
            if self._summary_estimate(candidate) <= limit:
                carry = candidate
                continue
            if not carry:
                raise RequestPreparationError(
                    "compaction_failed", "one history turn exceeds summary input limit"
                )
            previous_summary = await self._generate_summary(carry)
            carry = [
                UserMessage(
                    content=f"{COMPACTION_SUMMARY_PREFIX}\n{previous_summary}"
                ),
                *turn,
            ]
            if self._summary_estimate(carry) > limit:
                raise RequestPreparationError(
                    "compaction_failed", "summary and next turn exceed input limit"
                )
        return await self._generate_summary(carry)

    @staticmethod
    def _summary_estimate(messages: Sequence[Message]) -> int:
        return estimate_context_tokens(
            SUMMARY_SYSTEM_PROMPT, messages, bytes_per_token=2
        )

    async def _generate_summary(self, messages: Sequence[Message]) -> str:
        summary_text = await self._summarizer(messages)
        if len(summary_text.encode("utf-8")) > min(
            4096, self.context_window * 2
        ):
            raise RequestPreparationError(
                "compaction_failed", "generated summary is too long"
            )
        return summary_text

    @staticmethod
    def _view(
        messages: Sequence[Message],
        summary: UserMessage | None,
        compacted_count: int,
        tool_result_limit: int | None,
    ) -> list[Message]:
        if compacted_count > len(messages):
            raise ValueError("history is shorter than compacted_count")
        view: list[Message] = (
            [summary] if summary is not None else []
        ) + list(messages[compacted_count:])
        if tool_result_limit is None:
            return view
        bounded: list[Message] = []
        for message in view:
            if (
                isinstance(message, ToolResultMessage)
                and len(message.content) > tool_result_limit
            ):
                bounded.append(message.model_copy(update={
                    "content": (
                        message.content[:tool_result_limit]
                        + f"\n[tool output truncated in context view; "
                        f"original length {len(message.content)} characters]"
                    )
                }))
            else:
                bounded.append(message)
        return bounded


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
