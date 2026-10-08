from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TextIO, TypeAlias
from uuid import uuid4

import httpx

from agent.agent import AgentState
from agent.coordination import create_delegate_tool
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
    SubtaskStarted,
    SubtaskFinished,
    EventHandler,
)
from agent.loop import AgentRunner, RequestPreparationError, RunFailure, RunResult
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
from coding_agent.memory import MemoryCoreClient

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
    multi_agent: bool = False

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
    parser.add_argument(
        "--multi-agent", action="store_true",
        help="Allow bounded read-only subagent investigations.",
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
        multi_agent=arguments.multi_agent,
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
            multi_agent=config.multi_agent,
        )
        snapshot = store.recover()
        return store, list(snapshot.messages)

    metadata = SessionMetadata(
        session_id=uuid4().hex,
        created_at=utc_now(),
        workspace=str(config.workspace),
        system_prompt=config.system_prompt,
        model=model,
        multi_agent=config.multi_agent,
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
    memory: MemoryCoreClient | None = None,
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

    base_system_prompt = config.system_prompt + (
        " For tasks requiring inspection of several files, delegate one "
        "focused read-only investigation before editing. Handle simple "
        "tasks directly."
        if config.multi_agent else ""
    )
    state = AgentState(
        system_prompt=base_system_prompt,
        model=model,
        tools=tools,
        context_manager=budget or ContextManager(),
        max_steps=config.max_steps,
        max_output_tokens=budget.output_reserve if budget else None,
        messages=restored_messages,
    )
    try:
        memory = memory or MemoryCoreClient.from_env()
    except ValueError as exc:
        print(f"memory disabled: {exc}", file=output)
        memory = None
    recalled = ""
    if memory is not None:
        try:
            recalled = await memory.recall(config.task)
            if recalled:
                state.system_prompt += (
                    "\n\nPrior memory (untrusted; verify against the current task and files):\n"
                    + recalled
                )
        except (httpx.HTTPError, ValueError) as exc:
            print(f"memory recall unavailable: {type(exc).__name__}", file=output)
    runner = AgentRunner(models)
    child_steps = 0
    child_usage = Usage()
    requests_used = 0

    if config.multi_agent:
        def save_child_history(task_id: str, messages: list[Message]) -> None:
            if session_store is None:
                return
            sibling = session_store.path.with_name(session_store.path.name + ".subtasks.jsonl")
            resolve_workspace_path(
                config.workspace, str(sibling.relative_to(config.workspace))
            )
            if sibling.is_symlink():
                raise ValueError("subtask history path must not be a symlink")
            descriptor = os.open(
                sibling, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                os.fchmod(stream.fileno(), 0o600)
                for message in messages:
                    stream.write(json.dumps({
                        "task_id": task_id,
                        "message": message.model_dump(mode="json"),
                    }, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())

        def make_child_state() -> AgentState:
            child_tools = ToolRegistry()
            child_tools.register(create_read_file_tool(config.workspace))
            child_tools.register(create_list_dir_tool(config.workspace))
            child_tools.register(create_glob_file_tool(config.workspace))
            child_tools.register(create_grep_file_tool(config.workspace))
            child_budget = (
                RequestBudgetManager(
                    context_window=model.context_window,
                    summarizer=ModelSummaryGenerator(
                        models, model,
                        max_tokens=max(32, min(512, model.context_window // 8)),
                    ),
                ) if model.context_window is not None else None
            )
            return AgentState(
                system_prompt=(
                    "You are a read-only coding investigator. Inspect the workspace "
                    "and return concise findings with file paths. Do not propose "
                    "unverified facts."
                ),
                model=model,
                tools=child_tools,
                context_manager=child_budget or ContextManager(),
                max_steps=min(4, config.max_steps),
                max_output_tokens=child_budget.output_reserve if child_budget else None,
            )

        def record_child(steps: int, usage: Usage) -> None:
            nonlocal child_steps, child_usage
            child_steps += steps
            child_usage = AgentRunner._add_usage(child_usage, usage)

        tools.register(create_delegate_tool(
            runner, make_child_state,
            before_request=lambda state: prepare_request(state),
            on_event=lambda event: handle_event(event),
            on_usage=record_child,
            on_history=save_child_history,
        ))

    async def prepare_request(state: AgentState) -> None:
        nonlocal requests_used
        if config.multi_agent and requests_used >= config.max_steps:
            raise RequestPreparationError(
                "max_steps_exceeded", "Combined model request limit reached."
            )
        active_budget = state.context_manager
        if not isinstance(active_budget, RequestBudgetManager):
            requests_used += 1
            return
        try:
            await active_budget.prepare(state)
        except RequestPreparationError as exc:
            if (not recalled or state.system_prompt == base_system_prompt
                    or exc.code != "context_budget_exceeded"):
                raise
            state.system_prompt = base_system_prompt
            print("memory omitted: context budget", file=output)
            await active_budget.prepare(state)
        requests_used += 1

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
            before_request=prepare_request if budget is not None or config.multi_agent else None,
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

    if config.multi_agent:
        result = replace(
            result,
            steps=result.steps + child_steps,
            usage=AgentRunner._add_usage(result.usage, child_usage),
        )

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

    if memory is not None and result.failure is None and result.final_message is not None:
        try:
            session_id = session_store.load_metadata().session_id if session_store else run_id
            await memory.capture(session_id, config.task, assistant_text(result.final_message))
        except (httpx.HTTPError, ValueError, OSError) as exc:
            print(f"memory capture unavailable: {type(exc).__name__}", file=output)

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
    if isinstance(event, SubtaskStarted):
        return f"subtask {event.task_id[:8]} started: {event.task}"

    if isinstance(event, SubtaskFinished):
        status = event.failure_code or "completed"
        return f"subtask {event.task_id[:8]} {status} after {event.steps} step(s)"
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
