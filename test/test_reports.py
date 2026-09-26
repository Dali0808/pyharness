from __future__ import annotations

import json

import pytest

from agent.loop import RunFailure, RunResult
from ai.schemas import ToolResultMessage, Usage
from coding_agent.cli import TaskRunResult
from evals.evaluator import CaseEvaluationResult
from evals.reports import (
    TokenPricing,
    build_report,
    render_json,
    render_markdown,
    write_reports,
)
from evals.runner import EvalRunResult
from evals.scorers import ScoreResult, ToolCoverageResult


def make_evaluation_result(
    *,
    case_id: str,
    passed: bool,
    reason: str,
    exit_code: int = 0,
    steps: int = 1,
    elapsed_seconds: float = 0.1,
    usage: Usage | None = None,
    tool_error_count: int = 0,
    artifact_success: bool | None = None,
    expected_tool_names: tuple[str, ...] = ("write_file",),
    observed_tool_names: tuple[str, ...] = ("write_file",),
) -> CaseEvaluationResult:
    failure = (
        None
        if exit_code == 0
        else RunFailure(
            code="provider_error",
            message="provider unavailable",
        )
    )
    tool_errors = tuple(
        ToolResultMessage(
            tool_call_id=f"call-{index}",
            tool_name="read_file",
            content="tool_execution_failed: test error",
            is_error=True,
        )
        for index in range(tool_error_count)
    )
    run_result = RunResult(
        final_message=None,
        failure=failure,
        steps=steps,
        usage=usage or Usage(),
    )
    if artifact_success is None:
        artifact_success = passed
    missing_tool_names = tuple(
        sorted(set(expected_tool_names) - set(observed_tool_names))
    )

    return CaseEvaluationResult(
        case_id=case_id,
        run_result=EvalRunResult(
            case_id=case_id,
            task_result=TaskRunResult(
                exit_code=exit_code,
                run_result=run_result,
            ),
            elapsed_seconds=elapsed_seconds,
            final_files={},
            tool_errors=tool_errors,
        ),
        score=ScoreResult(
            passed=passed,
            score=1.0 if passed else 0.0,
            reason=reason,
        ),
        artifact_score=ScoreResult(
            passed=artifact_success,
            score=1.0 if artifact_success else 0.0,
            reason="artifact check",
        ),
        tool_coverage=ToolCoverageResult(
            expected_tool_names=expected_tool_names,
            observed_tool_names=observed_tool_names,
            missing_tool_names=missing_tool_names,
            passed=not missing_tool_names,
        ),
    )


def test_build_report_aggregates_case_results() -> None:
    results = [
        make_evaluation_result(
            case_id="passed-case",
            passed=True,
            reason="all expected file contents match",
            steps=2,
            elapsed_seconds=0.2,
            usage=Usage(
                input_tokens=100,
                output_tokens=20,
                total_tokens=120,
                cached_input_tokens=20,
            ),
        ),
        make_evaluation_result(
            case_id="failed-case",
            passed=False,
            reason="file content mismatch in: result.txt",
            exit_code=1,
            steps=4,
            elapsed_seconds=0.4,
            usage=Usage(
                input_tokens=60,
                output_tokens=10,
                total_tokens=70,
            ),
        ),
    ]

    report = build_report(results)

    assert report.summary.total_cases == 2
    assert report.summary.passed_cases == 1
    assert report.summary.failed_cases == 1
    assert report.summary.success_rate == 0.5
    assert report.summary.run_success_cases == 1
    assert report.summary.artifact_success_cases == 1
    assert report.summary.tool_coverage_cases == 2
    assert report.summary.total_steps == 6
    assert report.summary.average_steps == 3.0
    assert report.summary.total_elapsed_seconds == pytest.approx(
        0.6
    )
    assert report.summary.average_elapsed_seconds == pytest.approx(
        0.3
    )
    assert report.summary.usage == Usage(
        input_tokens=160,
        output_tokens=30,
        total_tokens=190,
        cached_input_tokens=20,
    )


def test_build_report_preserves_failed_case_details() -> None:
    result = make_evaluation_result(
        case_id="failed-case",
        passed=False,
        reason="missing expected file(s): result.txt",
        exit_code=1,
        steps=3,
        elapsed_seconds=0.25,
        tool_error_count=2,
    )

    report = build_report([result])
    case = report.cases[0]

    assert case.case_id == "failed-case"
    assert case.passed is False
    assert case.score == 0.0
    assert case.reason == "missing expected file(s): result.txt"
    assert case.exit_code == 1
    assert case.failure_code == "provider_error"
    assert case.steps == 3
    assert case.elapsed_seconds == 0.25
    assert case.tool_error_count == 2
    assert case.run_success is False
    assert case.artifact_success is False


def test_report_separates_run_artifact_and_tool_coverage() -> None:
    result = make_evaluation_result(
        case_id="failed-after-write",
        passed=False,
        reason="run failed before task completed",
        exit_code=1,
        artifact_success=True,
        expected_tool_names=("read_file", "write_file"),
        observed_tool_names=("write_file",),
    )

    report = build_report([result])
    case = report.cases[0]
    data = json.loads(render_json(report))

    assert case.passed is False
    assert case.run_success is False
    assert case.artifact_success is True
    assert case.tool_coverage_passed is False
    assert case.missing_tool_names == ("read_file",)
    assert report.summary.tool_coverage_rate == 0.0
    assert data["cases"][0]["missing_tool_names"] == ["read_file"]
    assert "| Tool coverage rate | 0.00% |" in render_markdown(report)


def test_build_report_leaves_cost_empty_without_pricing() -> None:
    result = make_evaluation_result(
        case_id="cost-case",
        passed=True,
        reason="passed",
        usage=Usage(
            input_tokens=1_000,
            output_tokens=500,
            total_tokens=1_500,
            cached_input_tokens=200,
        ),
    )

    report = build_report([result])

    assert report.cases[0].estimated_cost is None
    assert report.summary.total_estimated_cost is None


def test_build_report_calculates_cost_with_cached_tokens() -> None:
    result = make_evaluation_result(
        case_id="priced-case",
        passed=True,
        reason="passed",
        usage=Usage(
            input_tokens=1_000_000,
            output_tokens=500_000,
            total_tokens=1_500_000,
            cached_input_tokens=200_000,
        ),
    )
    pricing = TokenPricing(
        input_per_million_tokens=2.0,
        output_per_million_tokens=4.0,
        cached_input_per_million_tokens=0.5,
    )

    report = build_report(
        [result],
        pricing=pricing,
    )

    assert report.cases[0].estimated_cost == pytest.approx(
        3.7
    )
    assert report.summary.total_estimated_cost == pytest.approx(
        3.7
    )


def test_render_json_is_machine_readable() -> None:
    report = build_report(
        [
            make_evaluation_result(
                case_id="json-case",
                passed=True,
                reason="passed",
                steps=2,
                usage=Usage(
                    input_tokens=10,
                    output_tokens=5,
                    total_tokens=15,
                ),
            )
        ]
    )

    rendered = render_json(report)
    data = json.loads(rendered)

    assert data["summary"]["total_cases"] == 1
    assert data["summary"]["success_rate"] == 1.0
    assert data["summary"]["usage"]["total_tokens"] == 15
    assert data["cases"][0]["case_id"] == "json-case"
    assert data["cases"][0]["passed"] is True
    assert data["cases"][0]["estimated_cost"] is None


def test_render_markdown_contains_summary_and_case_table() -> None:
    report = build_report(
        [
            make_evaluation_result(
                case_id="passed-case",
                passed=True,
                reason="passed",
            ),
            make_evaluation_result(
                case_id="failed-case",
                passed=False,
                reason="missing expected file(s): result.txt",
            ),
        ]
    )

    rendered = render_markdown(report)

    assert "# Evaluation Report" in rendered
    assert "## Summary" in rendered
    assert "| Total cases | 2 |" in rendered
    assert "| Success rate | 50.00% |" in rendered
    assert "## Cases" in rendered
    assert "| passed-case | passed | 1.00 |" in rendered
    assert "| failed-case | failed | 0.00 |" in rendered
    assert "n/a" in rendered


def test_write_reports_writes_utf8_json_and_markdown(
    tmp_path,
) -> None:
    report = build_report(
        [
            make_evaluation_result(
                case_id="write-case",
                passed=True,
                reason="passed",
            )
        ]
    )
    json_path = tmp_path / "reports" / "result.json"
    markdown_path = tmp_path / "reports" / "result.md"

    write_reports(
        report,
        json_path=json_path,
        markdown_path=markdown_path,
    )

    assert json_path.read_text(
        encoding="utf-8"
    ) == render_json(report)
    assert markdown_path.read_text(
        encoding="utf-8"
    ) == render_markdown(report)
