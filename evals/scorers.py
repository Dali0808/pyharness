from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from evals.cases.model import EvalCase
from evals.runner import EvalRunResult


@dataclass(frozen=True, slots=True)
class ScoreResult:
    passed: bool
    score: float
    reason: str


@dataclass(frozen=True, slots=True)
class ToolCoverageResult:
    expected_tool_names: tuple[str, ...]
    observed_tool_names: tuple[str, ...]
    missing_tool_names: tuple[str, ...]
    passed: bool


def run_succeeded(result: EvalRunResult) -> bool:
    task_result = result.task_result
    return (
        task_result.exit_code == 0
        and task_result.run_result.failure is None
    )


def score_tool_coverage(
    case: EvalCase,
    result: EvalRunResult,
) -> ToolCoverageResult:
    expected = tuple(sorted(case.expected_tool_names))
    observed = tuple(sorted(set(result.tool_call_names)))
    missing = tuple(sorted(set(expected) - set(observed)))
    return ToolCoverageResult(
        expected_tool_names=expected,
        observed_tool_names=observed,
        missing_tool_names=missing,
        passed=not missing,
    )


def score_file_exists(
    result: EvalRunResult,
    paths: Sequence[str],
) -> ScoreResult:
    missing_paths = [
        path
        for path in paths
        if path not in result.final_files
    ]

    if missing_paths:
        return ScoreResult(
            passed=False,
            score=0.0,
            reason=(
                "missing expected file(s): "
                + ", ".join(missing_paths)
            ),
        )

    return ScoreResult(
        passed=True,
        score=1.0,
        reason="all expected files exist",
    )


def score_file_contents(
    result: EvalRunResult,
    expected_files: Mapping[str, str],
) -> ScoreResult:
    missing_paths = [
        path
        for path in expected_files
        if path not in result.final_files
    ]

    if missing_paths:
        return ScoreResult(
            passed=False,
            score=0.0,
            reason=(
                "missing expected file(s): "
                + ", ".join(missing_paths)
            ),
        )

    mismatches = [
        path
        for path, expected_content in expected_files.items()
        if result.final_files[path] != expected_content
    ]

    if mismatches:
        return ScoreResult(
            passed=False,
            score=0.0,
            reason=(
                "file content mismatch in: "
                + ", ".join(mismatches)
            ),
        )

    return ScoreResult(
        passed=True,
        score=1.0,
        reason="all expected file contents match",
    )


def score_json_file(
    result: EvalRunResult,
    path: str,
    expected: Any,
) -> ScoreResult:
    actual_content = result.final_files.get(path)

    if actual_content is None:
        return ScoreResult(
            passed=False,
            score=0.0,
            reason=f"missing expected JSON file: {path}",
        )

    try:
        actual_value = json.loads(actual_content)
    except json.JSONDecodeError as exc:
        return ScoreResult(
            passed=False,
            score=0.0,
            reason=(
                f"invalid JSON in {path}: "
                f"{exc.msg}"
            ),
        )

    if actual_value != expected:
        return ScoreResult(
            passed=False,
            score=0.0,
            reason=f"JSON value mismatch in: {path}",
        )

    return ScoreResult(
        passed=True,
        score=1.0,
        reason=f"JSON content matches: {path}",
    )


def score_tool_error_recovery(
    result: EvalRunResult,
    output_path: str,
    expected_content: str,
) -> ScoreResult:
    if not run_succeeded(result):
        return ScoreResult(
            passed=False,
            score=0.0,
            reason="run failed before recovery completed",
        )

    if not result.tool_errors:
        return ScoreResult(
            passed=False,
            score=0.0,
            reason="no tool error was observed",
        )

    output_score = score_file_contents(
        result,
        {
            output_path: expected_content,
        },
    )

    if not output_score.passed:
        return ScoreResult(
            passed=False,
            score=0.0,
            reason=(
                "tool error occurred, but recovery output "
                f"was invalid: {output_score.reason}"
            ),
        )

    return ScoreResult(
        passed=True,
        score=1.0,
        reason=(
            "tool error was observed and the recovery "
            "output is correct"
        ),
    )


def score_case(
    case: EvalCase,
    result: EvalRunResult,
) -> ScoreResult:
    if not case.expected_files:
        return ScoreResult(
            passed=False,
            score=0.0,
            reason="case has no deterministic expected files",
        )

    if case.requires_tool_error_recovery and len(case.expected_files) != 1:
        return ScoreResult(
            passed=False,
            score=0.0,
            reason=(
                "tool error recovery cases require exactly "
                "one expected file"
            ),
        )

    if case.requires_tool_error_recovery:
        output_path, expected_content = next(
            iter(case.expected_files.items())
        )
        return score_tool_error_recovery(
            result,
            output_path,
            expected_content,
        )

    if not run_succeeded(result):
        return ScoreResult(
            passed=False,
            score=0.0,
            reason="run failed before task completed",
        )

    return score_file_contents(result, case.expected_files)
