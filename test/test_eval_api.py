from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from ai.provider import ScriptedProvider
from ai.schemas import (
    AssistantMessage,
    ChatResponse,
    TextPart,
    ToolCallPart,
)
from evals.runner import EvalRunnerConfig
from coding_agent.cli import CliConfig
from evals.api import run_evaluation
from evals.cases.catalog import ALL_CASES
from evals.cases.model import EvalCase
from evals.evaluator import (
    CaseEvaluationResult,
    Evaluator,
)


class RecordingEvaluator(Evaluator):
    def __init__(
        self,
        results: Sequence[CaseEvaluationResult] = (),
    ) -> None:
        self.received_cases: tuple[EvalCase, ...] = ()
        self.received_factory = None
        self._results = list(results)

    async def evaluate_cases(
        self,
        cases: Sequence[EvalCase],
        provider_factory,
    ) -> list[CaseEvaluationResult]:
        self.received_cases = tuple(cases)
        self.received_factory = provider_factory
        return list(self._results)


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


def make_write_case() -> EvalCase:
    return EvalCase(
        case_id="api-write-case",
        task="Create result.txt.",
        expected_files={
            "result.txt": "generated content\n",
        },
        expected_tool_names=frozenset({"write_file"}),
    )


def make_provider() -> ScriptedProvider:
    return ScriptedProvider(
        [
            write_file_response(
                "result.txt",
                "generated content\n",
            ),
            final_response("Created result.txt."),
        ]
    )


class AlternateScriptedProvider(ScriptedProvider):
    id = "alternate"


@pytest.mark.asyncio
async def test_run_evaluation_uses_all_cases_by_default() -> None:
    evaluator = RecordingEvaluator()

    def provider_factory(
        config: CliConfig,
    ) -> ScriptedProvider:
        raise AssertionError(
            "recording evaluator must not create a provider"
        )

    run = await run_evaluation(
        provider_factory,
        evaluator=evaluator,
    )

    assert evaluator.received_cases == ALL_CASES
    assert evaluator.received_factory is provider_factory
    assert run.results == ()
    assert run.report.summary.total_cases == 0


@pytest.mark.asyncio
async def test_run_evaluation_evaluates_selected_cases_and_builds_report() -> None:
    case = make_write_case()
    provider = make_provider()

    run = await run_evaluation(
        lambda _: provider,
        cases=[case],
    )

    assert len(run.results) == 1
    assert run.results[0].case_id == case.case_id
    assert run.results[0].score.passed is True
    assert run.report.summary.total_cases == 1
    assert run.report.summary.passed_cases == 1
    assert run.report.summary.success_rate == 1.0
    assert run.report.cases[0].case_id == case.case_id
    assert run.report.cases[0].run_success is True
    assert run.report.cases[0].artifact_success is True
    assert run.report.cases[0].tool_coverage_passed is True


@pytest.mark.asyncio
async def test_run_evaluation_writes_requested_reports(
    tmp_path: Path,
) -> None:
    case = make_write_case()
    json_path = tmp_path / "reports" / "evaluation.json"
    markdown_path = tmp_path / "reports" / "evaluation.md"

    run = await run_evaluation(
        lambda _: make_provider(),
        cases=[case],
        json_path=json_path,
        markdown_path=markdown_path,
    )

    json_data = json.loads(
        json_path.read_text(encoding="utf-8")
    )
    markdown = markdown_path.read_text(encoding="utf-8")

    assert run.report.summary.total_cases == 1
    assert json_data["summary"]["total_cases"] == 1
    assert json_data["cases"][0]["case_id"] == case.case_id
    assert json_data["cases"][0]["observed_tool_names"] == [
        "write_file"
    ]
    assert "# Evaluation Report" in markdown
    assert case.case_id in markdown


@pytest.mark.asyncio
async def test_run_evaluation_rejects_partial_report_paths(
    tmp_path: Path,
) -> None:
    json_path = tmp_path / "evaluation.json"

    with pytest.raises(
        ValueError,
        match=(
            "json_path and markdown_path must be provided together"
        ),
    ):
        await run_evaluation(
            lambda _: ScriptedProvider([]),
            json_path=json_path,
        )

    assert not json_path.exists()


@pytest.mark.asyncio
async def test_run_evaluation_uses_runner_provider_configuration() -> None:
    case = make_write_case()
    provider = AlternateScriptedProvider(
        [
            write_file_response(
                "result.txt",
                "generated content\n",
            ),
            final_response(),
        ]
    )
    received_configs: list[CliConfig] = []

    def provider_factory(config: CliConfig) -> AlternateScriptedProvider:
        received_configs.append(config)
        return provider

    run = await run_evaluation(
        provider_factory,
        cases=[case],
        runner_config=EvalRunnerConfig(
            provider_id="alternate",
            model_id="alternate-model",
            base_url="https://provider.invalid",
            api_key_env="ALTERNATE_API_KEY",
        ),
    )

    assert run.results[0].score.passed is True
    assert received_configs[0].provider_id == "alternate"
    assert received_configs[0].model_id == "alternate-model"
    assert received_configs[0].base_url == "https://provider.invalid"
    assert received_configs[0].api_key_env == "ALTERNATE_API_KEY"


@pytest.mark.asyncio
async def test_run_evaluation_rejects_identical_report_paths(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "evaluation.json"

    with pytest.raises(
        ValueError,
        match="json_path and markdown_path must be different files",
    ):
        await run_evaluation(
            lambda _: ScriptedProvider([]),
            json_path=report_path,
            markdown_path=report_path,
        )

    assert not report_path.exists()
