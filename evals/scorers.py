from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from evals.runner import EvalRunResult


@dataclass(frozen=True, slots=True)
class ScoreResult:
    passed: bool
    score: float
    reason: str


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