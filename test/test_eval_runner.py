from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
import json

from ai.provider import ScriptedProvider
from ai.schemas import (
    AssistantMessage,
    ChatResponse,
    TextPart,
    ToolCallPart,
)
from coding_agent.cli import CliConfig
from evals.cases.model import EvalCase
from evals.runner import EvalRunner


def final_response(text: str = "Task completed.") -> ChatResponse:
    return ChatResponse(
        message=AssistantMessage(
            content=[
                TextPart(text=text),
            ],
        ),
        finish_reason="stop",
    )


def write_file_response(
    path: str,
    content: str,
) -> ChatResponse:
    return ChatResponse(
        message=AssistantMessage(
            content=[
                ToolCallPart(
                    id="call-write",
                    name="write_file",
                    arguments_json=json.dumps(
                        {
                            "path": path,
                            "content": content,
                        }
                    ),
                ),
            ],
        ),
        finish_reason="tool_calls",
    )


@dataclass
class ProviderFactoryRecorder:
    provider: ScriptedProvider
    configs: list[CliConfig]

    def __call__(
        self,
        config: CliConfig,
    ) -> ScriptedProvider:
        self.configs.append(config)
        return self.provider


@pytest.mark.asyncio
async def test_runner_seeds_initial_files() -> None:
    case = EvalCase(
        case_id="seed-files",
        task="Inspect the seeded file.",
        initial_files={
            "nested/input/task.txt": "seed content",
        },
        required_tools=frozenset({"read_file"}),
    )
    provider_configs: list[CliConfig] = []

    def provider_factory(
        config: CliConfig,
    ) -> ScriptedProvider:
        provider_configs.append(config)

        seeded_file = (
            config.workspace
            / "nested"
            / "input"
            / "task.txt"
        )
        assert seeded_file.read_text(
            encoding="utf-8",
        ) == "seed content"

        return ScriptedProvider(
            [final_response()],
        )

    result = await EvalRunner().run_case(
        case,
        provider_factory,
    )

    assert result.case_id == "seed-files"
    assert result.task_result.exit_code == 0
    assert len(provider_configs) == 1
    assert provider_configs[0].task == case.task
    assert result.final_files == {
        "nested/input/task.txt": "seed content",
    }


@pytest.mark.asyncio
async def test_runner_calls_run_task_with_case_task() -> None:
    case = EvalCase(
        case_id="task-forwarding",
        task="Complete this exact evaluation task.",
        required_tools=frozenset({"read_file"}),
    )
    recorder = ProviderFactoryRecorder(
        provider=ScriptedProvider(
            [final_response()],
        ),
        configs=[],
    )

    result = await EvalRunner().run_case(
        case,
        recorder,
    )

    assert result.task_result.exit_code == 0
    assert len(recorder.configs) == 1

    config = recorder.configs[0]
    assert config.task == case.task
    assert config.provider_id == "scripted"
    assert config.model_id == "eval-model"
    assert config.session_path is None


@pytest.mark.asyncio
async def test_runner_returns_final_workspace_files() -> None:
    case = EvalCase(
        case_id="write-file",
        task="Create the requested output file.",
        expected_files={
            "result.txt": "generated content",
        },
        required_tools=frozenset({"write_file"}),
    )
    provider = ScriptedProvider(
        [
            write_file_response(
                "result.txt",
                "generated content",
            ),
            final_response("File created."),
        ],
    )

    result = await EvalRunner().run_case(
        case,
        lambda _: provider,
    )

    assert result.task_result.exit_code == 0
    assert result.task_result.run_result.failure is None
    assert result.final_files == {
        "result.txt": "generated content",
    }


@pytest.mark.asyncio
async def test_runner_isolates_case_workspaces() -> None:
    first_case = EvalCase(
        case_id="first-case",
        task="Create a file.",
        required_tools=frozenset({"write_file"}),
    )
    second_case = EvalCase(
        case_id="second-case",
        task="Finish without creating files.",
        required_tools=frozenset({"read_file"}),
    )

    first_provider = ScriptedProvider(
        [
            write_file_response(
                "created.txt",
                "from first case",
            ),
            final_response(),
        ],
    )
    second_provider = ScriptedProvider(
        [final_response()],
    )

    configs: list[CliConfig] = []

    def first_factory(config: CliConfig) -> ScriptedProvider:
        configs.append(config)
        return first_provider

    def second_factory(config: CliConfig) -> ScriptedProvider:
        configs.append(config)
        return second_provider

    first_result = await EvalRunner().run_case(
        first_case,
        first_factory,
    )
    second_result = await EvalRunner().run_case(
        second_case,
        second_factory,
    )

    assert first_result.final_files == {
        "created.txt": "from first case",
    }
    assert second_result.final_files == {}
    assert configs[0].workspace != configs[1].workspace


class ClosableScriptedProvider(ScriptedProvider):
    def __init__(
        self,
        responses: list[ChatResponse],
    ) -> None:
        super().__init__(responses)
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_runner_closes_provider_after_run() -> None:
    case = EvalCase(
        case_id="provider-close",
        task="Complete the task.",
        required_tools=frozenset({"read_file"}),
    )
    provider = ClosableScriptedProvider(
        [final_response()],
    )

    result = await EvalRunner().run_case(
        case,
        lambda _: provider,
    )

    assert result.task_result.exit_code == 0
    assert provider.closed is True