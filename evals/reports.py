from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from math import isfinite
from pathlib import Path

from ai.schemas import Usage
from evals.evaluator import CaseEvaluationResult


@dataclass(frozen=True, slots=True)
class TokenPricing:
    input_per_million_tokens: float
    output_per_million_tokens: float
    cached_input_per_million_tokens: float

    def __post_init__(self) -> None:
        rates = (
            self.input_per_million_tokens,
            self.output_per_million_tokens,
            self.cached_input_per_million_tokens,
        )

        if any(
            not isfinite(rate) or rate < 0
            for rate in rates
        ):
            raise ValueError(
                "token pricing rates must be finite and non-negative"
            )


@dataclass(frozen=True, slots=True)
class CaseReport:
    case_id: str
    passed: bool
    score: float
    reason: str
    exit_code: int
    failure_code: str | None
    steps: int
    elapsed_seconds: float
    usage: Usage
    tool_error_count: int
    estimated_cost: float | None


@dataclass(frozen=True, slots=True)
class EvaluationSummary:
    total_cases: int
    passed_cases: int
    failed_cases: int
    success_rate: float
    total_steps: int
    average_steps: float
    total_elapsed_seconds: float
    average_elapsed_seconds: float
    usage: Usage
    total_estimated_cost: float | None


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    summary: EvaluationSummary
    cases: tuple[CaseReport, ...]


def build_report(
    results: Sequence[CaseEvaluationResult],
    *,
    pricing: TokenPricing | None = None,
) -> EvaluationReport:
    case_reports = tuple(
        _build_case_report(
            result,
            pricing=pricing,
        )
        for result in results
    )
    total_cases = len(case_reports)
    passed_cases = sum(
        case.passed
        for case in case_reports
    )
    total_steps = sum(
        case.steps
        for case in case_reports
    )
    total_elapsed_seconds = sum(
        case.elapsed_seconds
        for case in case_reports
    )
    usage = _add_usage(
        case.usage
        for case in case_reports
    )

    total_estimated_cost = (
        None
        if pricing is None
        else sum(
            case.estimated_cost or 0.0
            for case in case_reports
        )
    )

    return EvaluationReport(
        summary=EvaluationSummary(
            total_cases=total_cases,
            passed_cases=passed_cases,
            failed_cases=total_cases - passed_cases,
            success_rate=(
                passed_cases / total_cases
                if total_cases
                else 0.0
            ),
            total_steps=total_steps,
            average_steps=(
                total_steps / total_cases
                if total_cases
                else 0.0
            ),
            total_elapsed_seconds=total_elapsed_seconds,
            average_elapsed_seconds=(
                total_elapsed_seconds / total_cases
                if total_cases
                else 0.0
            ),
            usage=usage,
            total_estimated_cost=total_estimated_cost,
        ),
        cases=case_reports,
    )


def render_json(report: EvaluationReport) -> str:
    return json.dumps(
        _report_data(report),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def render_markdown(report: EvaluationReport) -> str:
    summary = report.summary
    lines = [
        "# Evaluation Report",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Total cases | {summary.total_cases} |",
        f"| Passed cases | {summary.passed_cases} |",
        f"| Failed cases | {summary.failed_cases} |",
        (
            "| Success rate | "
            f"{summary.success_rate:.2%} |"
        ),
        f"| Total steps | {summary.total_steps} |",
        (
            "| Average steps | "
            f"{summary.average_steps:.2f} |"
        ),
        (
            "| Total elapsed seconds | "
            f"{summary.total_elapsed_seconds:.6f} |"
        ),
        (
            "| Average elapsed seconds | "
            f"{summary.average_elapsed_seconds:.6f} |"
        ),
        (
            "| Input tokens | "
            f"{summary.usage.input_tokens} |"
        ),
        (
            "| Cached input tokens | "
            f"{summary.usage.cached_input_tokens} |"
        ),
        (
            "| Output tokens | "
            f"{summary.usage.output_tokens} |"
        ),
        (
            "| Total tokens | "
            f"{summary.usage.total_tokens} |"
        ),
        (
            "| Estimated cost | "
            f"{_render_cost(summary.total_estimated_cost)} |"
        ),
        "",
        "## Cases",
        "",
        (
            "| Case ID | Passed | Score | Steps | "
            "Elapsed seconds | Total tokens | "
            "Tool errors | Estimated cost | Reason |"
        ),
        (
            "| --- | --- | --- | --- | --- | --- | "
            "--- | --- | --- |"
        ),
    ]

    for case in report.cases:
        lines.append(
            "| "
            f"{_markdown_cell(case.case_id)} | "
            f"{'passed' if case.passed else 'failed'} | "
            f"{case.score:.2f} | "
            f"{case.steps} | "
            f"{case.elapsed_seconds:.6f} | "
            f"{case.usage.total_tokens} | "
            f"{case.tool_error_count} | "
            f"{_render_cost(case.estimated_cost)} | "
            f"{_markdown_cell(case.reason)} |"
        )

    return "\n".join(lines) + "\n"


def write_reports(
    report: EvaluationReport,
    *,
    json_path: Path,
    markdown_path: Path,
) -> None:
    json_path = Path(json_path)
    markdown_path = Path(markdown_path)

    json_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    markdown_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    json_path.write_text(
        render_json(report),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_markdown(report),
        encoding="utf-8",
    )


def _build_case_report(
    result: CaseEvaluationResult,
    *,
    pricing: TokenPricing | None,
) -> CaseReport:
    run_result = result.run_result
    task_result = run_result.task_result
    usage = task_result.run_result.usage
    failure = task_result.run_result.failure

    return CaseReport(
        case_id=result.case_id,
        passed=result.score.passed,
        score=result.score.score,
        reason=result.score.reason,
        exit_code=task_result.exit_code,
        failure_code=(
            failure.code
            if failure is not None
            else None
        ),
        steps=task_result.run_result.steps,
        elapsed_seconds=run_result.elapsed_seconds,
        usage=usage,
        tool_error_count=len(run_result.tool_errors),
        estimated_cost=_estimate_cost(
            usage,
            pricing,
        ),
    )


def _add_usage(usages: Iterable[Usage]) -> Usage:
    usage_items = tuple(usages)

    return Usage(
        input_tokens=sum(
            usage.input_tokens
            for usage in usage_items
        ),
        output_tokens=sum(
            usage.output_tokens
            for usage in usage_items
        ),
        total_tokens=sum(
            usage.total_tokens
            for usage in usage_items
        ),
        cached_input_tokens=sum(
            usage.cached_input_tokens
            for usage in usage_items
        ),
    )


def _estimate_cost(
    usage: Usage,
    pricing: TokenPricing | None,
) -> float | None:
    if pricing is None:
        return None

    uncached_input_tokens = (
        usage.input_tokens
        - usage.cached_input_tokens
    )

    if uncached_input_tokens < 0:
        raise ValueError(
            "cached_input_tokens cannot exceed input_tokens"
        )

    return (
        (
            uncached_input_tokens
            * pricing.input_per_million_tokens
        )
        + (
            usage.cached_input_tokens
            * pricing.cached_input_per_million_tokens
        )
        + (
            usage.output_tokens
            * pricing.output_per_million_tokens
        )
    ) / 1_000_000


def _report_data(
    report: EvaluationReport,
) -> dict[str, object]:
    return {
        "summary": {
            "total_cases": report.summary.total_cases,
            "passed_cases": report.summary.passed_cases,
            "failed_cases": report.summary.failed_cases,
            "success_rate": report.summary.success_rate,
            "total_steps": report.summary.total_steps,
            "average_steps": report.summary.average_steps,
            "total_elapsed_seconds": (
                report.summary.total_elapsed_seconds
            ),
            "average_elapsed_seconds": (
                report.summary.average_elapsed_seconds
            ),
            "usage": _usage_data(report.summary.usage),
            "total_estimated_cost": (
                report.summary.total_estimated_cost
            ),
        },
        "cases": [
            {
                "case_id": case.case_id,
                "passed": case.passed,
                "score": case.score,
                "reason": case.reason,
                "exit_code": case.exit_code,
                "failure_code": case.failure_code,
                "steps": case.steps,
                "elapsed_seconds": case.elapsed_seconds,
                "usage": _usage_data(case.usage),
                "tool_error_count": case.tool_error_count,
                "estimated_cost": case.estimated_cost,
            }
            for case in report.cases
        ],
    }


def _usage_data(usage: Usage) -> dict[str, int]:
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
        "cached_input_tokens": usage.cached_input_tokens,
    }


def _render_cost(cost: float | None) -> str:
    if cost is None:
        return "n/a"

    return f"{cost:.6f}"


def _markdown_cell(value: str) -> str:
    return value.replace(
        "|",
        "\\|",
    ).replace(
        "\n",
        "<br>",
    )