from __future__ import annotations

import argparse
import asyncio
import inspect
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO, TypeAlias
from uuid import uuid4

from agent.agent import AgentState
from agent.events import (
    AgentEvent,
    ModelRequested,
    ModelResponded,
    RunFailed,
    RunFinished,
    RunStarted,
    ToolFinished,
    ToolStarted,
    EventHandler,
)
from agent.loop import AgentRunner, RunResult
from agent.tools import ToolRegistry
from agent.context import ContextManager
from ai.openai_compatible import OpenAICompatibleProvider
from ai.provider import LLMProvider, ModelRegistry
from ai.schemas import (
    AssistantMessage,
    Message,
    ModelSpec,
    TextPart,
    UserMessage,
    utc_now,
)
from coding_agent.builtins import (
    create_list_dir_tool,
    create_read_file_tool,
    create_write_file_tool,
    resolve_workspace_path,
)
from coding_agent.session import (
    JsonlSessionStore,
    SessionMetadata,
)
from coding_agent.compaction import (
    CompactedContextManager,
    ModelSummaryGenerator,
    compact_history,
    should_compact,
)

DEFAULT_SYSTEM_PROMPT = (
    "You are a coding assistant. Work only through the available "
    "workspace tools."
)
DEFAULT_KEEP_RECENT_TURNS = 2

@dataclass(frozen=True, slots=True)
class CliConfig:
    task: str
    workspace: Path
    provider_id: str
    model_id: str
    base_url: str
    api_key_env: str
    system_prompt: str
    max_steps: int
    session_path: Path | None = None
    context_window: int | None = None

@dataclass(frozen=True, slots=True)
class TaskRunResult:
    exit_code: int
    run_result: RunResult

@dataclass(frozen=True, slots=True)
class PreparedContext:
    context_manager: ContextManager
    compacted_count: int = 0

ProviderFactory: TypeAlias = Callable[[CliConfig], LLMProvider]


def parse_args(argv: Sequence[str] | None = None) -> CliConfig:
    parser = argparse.ArgumentParser(
        description="Run a workspace-scoped coding agent task."
    )
    parser.add_argument("task", help="Task for the coding agent.")
    parser.add_argument(
        "--workspace",
        default=".",
        help="Workspace root. Defaults to the current directory.",
    )
    parser.add_argument(
        "--session",
        default=None,
        help=(
            "Relative path to a JSONL session file inside "
            "the workspace."
        ),
    )
    parser.add_argument(
        "--provider-id",
        default="openai",
        help="Provider identifier used for model routing.",
    )
    parser.add_argument(
        "--model",
        required=True,
        help="Model identifier.",
    )
    parser.add_argument(
        "--context-window",
        type=_positive_int,
        default=None,
        help=(
            "Model context window in tokens. "
            "Enables automatic context compaction."
        ),
    )
    parser.add_argument(
        "--base-url",
        default="https://api.openai.com/v1",
        help="OpenAI-compatible API base URL.",
    )
    parser.add_argument(
        "--api-key-env",
        default="OPENAI_API_KEY",
        help="Environment variable containing the API key.",
    )
    parser.add_argument(
        "--system-prompt",
        default=DEFAULT_SYSTEM_PROMPT,
        help="System prompt for the agent.",
    )
    parser.add_argument(
        "--max-steps",
        type=_positive_int,
        default=10,
        help="Maximum number of model calls.",
    )

    arguments = parser.parse_args(argv)
    workspace = Path(arguments.workspace).resolve(strict=False)

    if not workspace.is_dir():
        parser.error(f"workspace is not a directory: {workspace}")

    session_path: Path | None = None

    if arguments.session is not None:
        try:
            session_path = resolve_workspace_path(
                workspace,
                arguments.session,
            )
        except (OSError, ValueError) as exc:
            parser.error(f"invalid session path: {exc}")

    return CliConfig(
        task=arguments.task,
        workspace=workspace,
        provider_id=arguments.provider_id,
        model_id=arguments.model,
        base_url=arguments.base_url,
        api_key_env=arguments.api_key_env,
        system_prompt=arguments.system_prompt,
        max_steps=arguments.max_steps,
        session_path=session_path,
        context_window=arguments.context_window,
    )


def create_provider(config: CliConfig) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        provider_id=config.provider_id,
        base_url=config.base_url,
        api_key=os.environ.get(config.api_key_env),
    )


def prepare_session(
    config: CliConfig,
    model: ModelSpec,
) -> tuple[JsonlSessionStore | None, list[Message]]:
    if config.session_path is None:
        return None, []

    store = JsonlSessionStore(config.session_path)

    if config.session_path.exists():
        snapshot = store.load()
        return store, list(snapshot.messages)

    metadata = SessionMetadata(
        session_id=uuid4().hex,
        created_at=utc_now(),
        workspace=str(config.workspace),
        system_prompt=config.system_prompt,
        model=model,
    )
    store.create(metadata)

    return store, []


def handle_run_event(
    event: AgentEvent,
    output: TextIO,
    session_store: JsonlSessionStore | None,
) -> None:
    print(render_event(event), file=output)

    if session_store is None:
        return

    if isinstance(event, ModelResponded):
        session_store.append_message(event.response.message)
    elif isinstance(event, ToolFinished):
        session_store.append_message(event.result)


async def prepare_context_manager(
    config: CliConfig,
    model: ModelSpec,
    models: ModelRegistry,
    restored_messages: Sequence[Message],
    user_message: UserMessage,
) -> PreparedContext:
    candidate_messages = [
        *restored_messages,
        user_message,
    ]

    if not should_compact(
        config.system_prompt,
        candidate_messages,
        context_window=model.context_window,
    ):
        return PreparedContext(
            context_manager=ContextManager(),
        )

    compaction = await compact_history(
        candidate_messages,
        keep_recent_turns=DEFAULT_KEEP_RECENT_TURNS,
        summarizer=ModelSummaryGenerator(
            models,
            model,
        ),
    )

    if compaction.compacted_count == 0:
        return PreparedContext(
            context_manager=ContextManager(),
        )

    return PreparedContext(
        context_manager=CompactedContextManager(
            summary_message=compaction.messages[0],
            compacted_count=compaction.compacted_count,
        ),
        compacted_count=compaction.compacted_count,
    )


async def run_task(
    config: CliConfig,
    provider: LLMProvider,
    output: TextIO,
    *,
    on_event: EventHandler | None = None,
) -> TaskRunResult:
    model = ModelSpec(
        provider=config.provider_id,
        id=config.model_id,
        context_window=config.context_window,
    )
    session_store, restored_messages = prepare_session(
        config,
        model,
    )

    models = ModelRegistry()
    models.register(model, provider)

    tools = ToolRegistry()
    tools.register(create_read_file_tool(config.workspace))
    tools.register(create_write_file_tool(config.workspace))
    tools.register(create_list_dir_tool(config.workspace))

    user_message = UserMessage(content=config.task)
    prepared_context = await prepare_context_manager(
        config,
        model,
        models,
        restored_messages,
        user_message,
    )

    if prepared_context.compacted_count > 0:
        message_word = (
            "message"
            if prepared_context.compacted_count == 1
            else "messages"
        )
        print(
            "context compacted: summarized "
            f"{prepared_context.compacted_count} {message_word}",
            file=output,
        )

    state = AgentState(
        system_prompt=config.system_prompt,
        model=model,
        tools=tools,
        context_manager=prepared_context.context_manager,
        max_steps=config.max_steps,
        messages=restored_messages,
    )
    runner = AgentRunner(models)



    if session_store is not None:
        session_store.append_message(user_message)

    def handle_event(event: AgentEvent) -> None:
        handle_run_event(
            event,
            output,
            session_store,
        )

        if on_event is not None:
            on_event(event)

    result = await runner.run(
        state,
        user_message,
        on_event=handle_event,
    )

    if result.failure is not None:
        print(
            f"failed ({result.failure.code}): {result.failure.message}",
            file=output,
        )
        return TaskRunResult(
            exit_code=1,
            run_result=result,
        )

    if result.final_message is None:
        print("failed: agent returned no final message", file=output)
        return TaskRunResult(
            exit_code=1,
            run_result=result,
        )

    print(
        f"answer: {assistant_text(result.final_message)}",
        file=output,
    )
    return TaskRunResult(
        exit_code=0,
        run_result=result,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    provider_factory: ProviderFactory | None = None,
    output: TextIO | None = None,
) -> int:
    config = parse_args(argv)
    factory = provider_factory or create_provider
    stream = output if output is not None else sys.stdout

    return asyncio.run(_run_main(config, factory, stream))


async def _run_main(
    config: CliConfig,
    provider_factory: ProviderFactory,
    output: TextIO,
) -> int:
    provider = provider_factory(config)

    try:
        task_result = await run_task(
            config,
            provider,
            output,
        )
        return task_result.exit_code
    finally:
        await close_provider(provider)


async def close_provider(provider: LLMProvider) -> None:
    close = getattr(provider, "aclose", None)
    if close is None:
        return

    result = close()
    if inspect.isawaitable(result):
        await result


def render_event(event: AgentEvent) -> str:
    if isinstance(event, RunStarted):
        return "run started"

    if isinstance(event, ModelRequested):
        return f"step {event.step}: requesting model"

    if isinstance(event, ModelResponded):
        return f"step {event.step}: model responded"

    if isinstance(event, ToolStarted):
        return f"step {event.step}: tool started: {event.call.name}"

    if isinstance(event, ToolFinished):
        status = "failed" if event.result.is_error else "finished"
        return (
            f"step {event.step}: tool {status}: "
            f"{event.result.tool_name}"
        )

    if isinstance(event, RunFinished):
        return f"run finished after {event.steps} step(s)"

    if isinstance(event, RunFailed):
        return (
            f"run failed after {event.steps} step(s): {event.code}"
        )

    raise TypeError(f"unsupported event: {type(event).__name__}")


def assistant_text(message: AssistantMessage) -> str:
    return "".join(
        part.text
        for part in message.content
        if isinstance(part, TextPart)
    )


def _positive_int(value: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "must be a positive integer"
        ) from exc

    if result <= 0:
        raise argparse.ArgumentTypeError(
            "must be a positive integer"
        )

    return result


if __name__ == "__main__":
    raise SystemExit(main())