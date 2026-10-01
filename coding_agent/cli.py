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
    ModelTextDelta,
    RunFailed,
    RunFinished,
    RunStarted,
    ToolFinished,
    ToolStarted,
    EventHandler,
)
from agent.loop import AgentRunner, RunFailure, RunResult
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
    Usage,
    utc_now,
)
from coding_agent.builtins import (
    Approval,
    create_edit_file_tool,
    create_glob_file_tool,
    create_grep_file_tool,
    create_list_dir_tool,
    create_read_file_tool,
    create_write_file_tool,
    resolve_workspace_path,
)
from coding_agent.session import (
    JsonlSessionStore,
    SessionCompatibilityError,
    SessionFormatError,
    SessionMetadata,
    validate_session_metadata,
)
from coding_agent.compaction import (
    ModelSummaryGenerator,
    RequestBudgetManager,
)
from coding_agent.command import CommandApproval, create_run_command_tool, format_command

DEFAULT_SYSTEM_PROMPT = (
    "You are a coding assistant. Work only through the available "
    "workspace tools."
)

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
        validate_session_metadata(
            store.load_metadata(),
            workspace=config.workspace,
            model=model,
            system_prompt=config.system_prompt,
        )
        snapshot = store.recover()
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
    *,
    run_id: str | None = None,
) -> None:
    if not isinstance(event, ModelTextDelta):
        print(render_event(event), file=output)

    if session_store is None:
        return

    if isinstance(event, ModelResponded):
        session_store.append_message(
            event.response.message, run_id=run_id
        )
    elif isinstance(event, ToolFinished):
        session_store.append_message(
            event.result, run_id=run_id
        )


async def run_task(
    config: CliConfig,
    provider: LLMProvider,
    output: TextIO,
    *,
    on_event: EventHandler | None = None,
    approve: Approval | None = None,
    approve_command: CommandApproval | None = None,
) -> TaskRunResult:
    model = ModelSpec(
        provider=config.provider_id,
        id=config.model_id,
        context_window=config.context_window,
    )
    try:
        session_store, restored_messages = prepare_session(config, model)
    except (SessionCompatibilityError, SessionFormatError, OSError) as exc:
        failure = RunFailure(code="session_error", message=str(exc))
        print(f"failed ({failure.code}): {failure.message}", file=output)
        return TaskRunResult(
            exit_code=1,
            run_result=RunResult(
                final_message=None, failure=failure, steps=0, usage=Usage()
            ),
        )

    models = ModelRegistry()
    models.register(model, provider)

    tools = ToolRegistry()
    approval = approve or _allow_change
    tools.register(create_read_file_tool(config.workspace))
    tools.register(create_write_file_tool(config.workspace, approval))
    tools.register(create_list_dir_tool(config.workspace))
    tools.register(create_glob_file_tool(config.workspace))
    tools.register(create_grep_file_tool(config.workspace))
    tools.register(create_edit_file_tool(config.workspace, approval))
    tools.register(create_run_command_tool(
        config.workspace, approve_command or _deny_command
    ))

    user_message = UserMessage(content=config.task)
    budget: RequestBudgetManager | None = None
    if model.context_window is not None:
        def report_compaction(count: int) -> None:
            noun = "message" if count == 1 else "messages"
            print(f"context compacted: summarized {count} {noun}", file=output)

        budget = RequestBudgetManager(
            context_window=model.context_window,
            summarizer=ModelSummaryGenerator(
                models,
                model,
                max_tokens=max(32, min(512, model.context_window // 8)),
            ),
            on_compaction=report_compaction,
        )

    state = AgentState(
        system_prompt=config.system_prompt,
        model=model,
        tools=tools,
        context_manager=budget or ContextManager(),
        max_steps=config.max_steps,
        max_output_tokens=budget.output_reserve if budget else None,
        messages=restored_messages,
    )
    runner = AgentRunner(models)

    run_id = uuid4().hex
    turn_id = uuid4().hex
    if session_store is not None:
        session_store.start_run(run_id, turn_id)
        session_store.append_message(
            user_message, run_id=run_id
        )

    def handle_event(event: AgentEvent) -> None:
        handle_run_event(
            event,
            output,
            session_store,
            run_id=run_id,
        )

        if on_event is not None:
            on_event(event)

    try:
        result = await runner.run(
            state,
            user_message,
            on_event=handle_event,
            before_request=budget.prepare if budget is not None else None,
        )
    except asyncio.CancelledError:
        result = RunResult(
            final_message=None,
            failure=RunFailure(
                code="cancelled",
                message="The current run was stopped.",
            ),
            steps=0,
            usage=Usage(),
        )
        handle_event(RunFailed(
            code="cancelled",
            message=result.failure.message,
            steps=0,
        ))

    if session_store is not None:
        run_completed = result.failure is None and result.final_message is not None
        session_store.end_run(
            run_id,
            "completed" if run_completed else (
                "interrupted"
                if result.failure is not None
                and result.failure.code == "cancelled"
                else "failed"
            ),
            failure_code=(
                result.failure.code
                if result.failure
                else (None if run_completed else "missing_final_message")
            ),
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
    approve: Approval | None = None,
    approve_command: CommandApproval | None = None,
) -> int:
    config = parse_args(argv)
    factory = provider_factory or create_provider
    stream = output if output is not None else sys.stdout
    if approve is None:
        approve = (
            lambda diff: _cli_approval(diff, stream)
            if provider_factory is None
            else _allow_change(diff)
        )

    return asyncio.run(_run_main(
        config, factory, stream, approve,
        approve_command or (lambda argv, cwd: _cli_command_approval(argv, cwd, stream)),
    ))


async def _run_main(
    config: CliConfig,
    provider_factory: ProviderFactory,
    output: TextIO,
    approve: Approval,
    approve_command: CommandApproval,
) -> int:
    provider = provider_factory(config)

    try:
        task_result = await run_task(
            config,
            provider,
            output,
            approve=approve,
            approve_command=approve_command,
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


def _allow_change(_: str) -> bool:
    return True


def _deny_command(_: list[str], __: Path) -> bool:
    return False


def _cli_approval(diff: str, output: TextIO) -> bool:
    print("proposed change:", file=output)
    print(diff, file=output, end="" if diff.endswith("\n") else "\n")
    if not sys.stdin.isatty():
        return False
    answer = input("Apply this change? [y/N] ")
    return answer.strip().lower() in {"y", "yes"}


def _cli_command_approval(argv: list[str], cwd: Path, output: TextIO) -> bool:
    print(format_command(argv, cwd), file=output)
    if not sys.stdin.isatty():
        return False
    answer = input("Run this command? [y/N] ")
    return answer.strip().lower() in {"y", "yes"}


def render_event(event: AgentEvent) -> str:
    if isinstance(event, RunStarted):
        return "run started"

    if isinstance(event, ModelRequested):
        return f"step {event.step}: requesting model"

    if isinstance(event, ModelTextDelta):
        return f"step {event.step}: model streamed text"

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
