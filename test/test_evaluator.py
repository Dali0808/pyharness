from __future__ import annotations

import json

import pytest

from ai.provider import ScriptedProvider
from ai.schemas import (
    AssistantMessage,
    ChatResponse,
    TextPart,
    ToolCallPart,
)
from coding_agent.cli import CliConfig
from evals.cases.model import EvalCase
from evals.cases.smoke import (
    READ_THEN_WRITE_CASE,
    SMOKE_CASES,
    TOOL_ERROR_RECOVERY_CASE,
    WRITE_FILE_CASE,
)
from evals.evaluator import Evaluator


def final_response(
    text: str = "Task completed.",
) -> ChatResponse:
    return ChatResponse(
        message=AssistantMessage(
            content=[
                TextPart(text=text),
            ],
        ),
        finish_reason="stop",
    )


def tool_response(
    *,
    call_id: str,
    name: str,
    arguments: dict[str, str],
) -> ChatResponse:
    return ChatResponse(
        message=AssistantMessage(
            content=[
                ToolCallPart(
                    id=call_id,
                    name=name,
                    arguments_json=json.dumps(arguments),
                ),
            ],
        ),
        finish_reason="tool_calls",
    )


def write_file_response(
    *,
    call_id: str,
    path: str,
    content: str,
) -> ChatResponse:
    return tool_response(
        call_id=call_id,
        name="write_file",
        arguments={
            "path": path,
            "content": content,
        },
    )


def read_file_response(
    *,
    call_id: str,
    path: str,
) -> ChatResponse:
    return tool_response(
        call_id=call_id,
        name="read_file",
        arguments={"path": path},
    )


def list_dir_response(
    *,
    call_id: str,
    path: str,
) -> ChatResponse:
    return tool_response(
        call_id=call_id,
        name="list_dir",
        arguments={"path": path},
    )


def missing_tool_response() -> ChatResponse:
    return tool_response(
        call_id="call-missing",
        name="missing_tool",
        arguments={},
    )


def smoke_provider_factory(
    config: CliConfig,
) -> ScriptedProvider:
    providers = {
        SMOKE_CASES[0].task: ScriptedProvider(
            [
                read_file_response(
                    call_id="read-task",
                    path="notes/task.txt",
                ),
                write_file_response(
                    call_id="write-copy",
                    path="copied-task.txt",
                    content=(
                        "Prepare a short project status "
                        "summary.\n"
                    ),
                ),
                final_response(),
            ]
        ),
        SMOKE_CASES[1].task: ScriptedProvider(
            [
                write_file_response(
                    call_id="write-greeting",
                    path="greeting.txt",
                    content="Hello from pyharness.\n",
                ),
                final_response(),
            ]
        ),
        SMOKE_CASES[2].task: ScriptedProvider(
            [
                list_dir_response(
                    call_id="list-src",
                    path="src",
                ),
                write_file_response(
                    call_id="write-manifest",
                    path="manifest.txt",
                    content="main.py\nutils.py\n",
                ),
                final_response(),
            ]
        ),
        SMOKE_CASES[3].task: ScriptedProvider(
            [
                read_file_response(
                    call_id="read-requirements",
                    path="input/requirements.txt",
                ),
                write_file_response(
                    call_id="write-requirements",
                    path="copied-requirements.txt",
                    content="Python >= 3.11\npytest\n",
                ),
                final_response(),
            ]
        ),
        SMOKE_CASES[4].task: ScriptedProvider(
            [
                missing_tool_response(),
                read_file_response(
                    call_id="read-actual",
                    path="input/actual.txt",
                ),
                write_file_response(
                    call_id="write-recovered",
                    path="recovered.txt",
                    content="Recovered successfully.\n",
                ),
                final_response(),
            ]
        ),
    }

    return providers[config.task]


@pytest.mark.asyncio
async def test_evaluator_scores_write_file_case_end_to_end() -> None:
    provider = ScriptedProvider(
        [
            write_file_response(
                call_id="write-greeting",
                path="greeting.txt",
                content="Hello from pyharness.\n",
            ),
            final_response(),
        ]
    )

    result = await Evaluator().evaluate_case(
        WRITE_FILE_CASE,
        lambda _: provider,
    )

    assert result.case_id == WRITE_FILE_CASE.case_id
    assert result.run_result.task_result.exit_code == 0
    assert result.run_result.final_files == {
        "greeting.txt": "Hello from pyharness.\n",
    }
    assert result.score.passed is True
    assert result.score.score == 1.0


@pytest.mark.asyncio
async def test_evaluator_scores_read_then_write_case_end_to_end() -> None:
    provider = ScriptedProvider(
        [
            read_file_response(
                call_id="read-requirements",
                path="input/requirements.txt",
            ),
            write_file_response(
                call_id="write-requirements",
                path="copied-requirements.txt",
                content="Python >= 3.11\npytest\n",
            ),
            final_response(),
        ]
    )

    result = await Evaluator().evaluate_case(
        READ_THEN_WRITE_CASE,
        lambda _: provider,
    )

    assert result.run_result.task_result.exit_code == 0
    assert result.score.passed is True
    assert result.score.score == 1.0


@pytest.mark.asyncio
async def test_evaluator_scores_tool_error_recovery_case_end_to_end() -> None:
    provider = ScriptedProvider(
        [
            missing_tool_response(),
            read_file_response(
                call_id="read-actual",
                path="input/actual.txt",
            ),
            write_file_response(
                call_id="write-recovered",
                path="recovered.txt",
                content="Recovered successfully.\n",
            ),
            final_response(),
        ]
    )

    result = await Evaluator().evaluate_case(
        TOOL_ERROR_RECOVERY_CASE,
        lambda _: provider,
    )

    assert result.run_result.task_result.exit_code == 0
    assert len(result.run_result.tool_errors) == 1
    assert result.score.passed is True
    assert result.score.score == 1.0


@pytest.mark.asyncio
async def test_evaluator_returns_smoke_results_in_case_order() -> None:
    results = await Evaluator().evaluate_cases(
        SMOKE_CASES,
        smoke_provider_factory,
    )

    assert [
        result.case_id
        for result in results
    ] == [
        case.case_id
        for case in SMOKE_CASES
    ]
    assert all(result.score.passed for result in results)
    assert [result.score.score for result in results] == [
        1.0,
        1.0,
        1.0,
        1.0,
        1.0,
    ]


@pytest.mark.asyncio
async def test_evaluator_reports_failed_score_for_wrong_output() -> None:
    case = EvalCase(
        case_id="wrong-output",
        task="Create result.txt.",
        expected_files={
            "result.txt": "expected content",
        },
        required_tools=frozenset({"write_file"}),
    )
    provider = ScriptedProvider(
        [
            write_file_response(
                call_id="write-result",
                path="result.txt",
                content="actual content",
            ),
            final_response(),
        ]
    )

    result = await Evaluator().evaluate_case(
        case,
        lambda _: provider,
    )

    assert result.run_result.task_result.exit_code == 0
    assert result.score.passed is False
    assert result.score.score == 0.0
    assert "mismatch" in result.score.reason