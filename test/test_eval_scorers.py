from __future__ import annotations

from agent.loop import RunResult
from ai.schemas import Usage
from coding_agent.cli import TaskRunResult
from evals.runner import EvalRunResult
from evals.scorers import (
    ScoreResult,
    score_file_contents,
    score_file_exists,
    score_json_file,
)


def make_result(
    files: dict[str, str],
) -> EvalRunResult:
    run_result = RunResult(
        final_message=None,
        failure=None,
        steps=1,
        usage=Usage(),
    )

    return EvalRunResult(
        case_id="scorer-test",
        task_result=TaskRunResult(
            exit_code=0,
            run_result=run_result,
        ),
        elapsed_seconds=0.01,
        final_files=files,
    )


def test_score_file_exists_passes_for_present_file() -> None:
    result = make_result(
        {
            "result.txt": "completed",
        }
    )

    score = score_file_exists(
        result,
        ["result.txt"],
    )

    assert score == ScoreResult(
        passed=True,
        score=1.0,
        reason="all expected files exist",
    )


def test_score_file_exists_fails_for_missing_file() -> None:
    result = make_result({})

    score = score_file_exists(
        result,
        ["result.txt"],
    )

    assert score.passed is False
    assert score.score == 0.0
    assert "result.txt" in score.reason
    assert "missing" in score.reason


def test_score_file_contents_passes_for_exact_match() -> None:
    result = make_result(
        {
            "result.txt": "expected content",
        }
    )

    score = score_file_contents(
        result,
        {
            "result.txt": "expected content",
        },
    )

    assert score.passed is True
    assert score.score == 1.0


def test_score_file_contents_reports_content_mismatch() -> None:
    result = make_result(
        {
            "result.txt": "actual content",
        }
    )

    score = score_file_contents(
        result,
        {
            "result.txt": "expected content",
        },
    )

    assert score.passed is False
    assert score.score == 0.0
    assert "result.txt" in score.reason
    assert "mismatch" in score.reason


def test_score_file_contents_reports_missing_file() -> None:
    result = make_result({})

    score = score_file_contents(
        result,
        {
            "result.txt": "expected content",
        },
    )

    assert score.passed is False
    assert score.score == 0.0
    assert "result.txt" in score.reason
    assert "missing" in score.reason


def test_score_json_file_accepts_equivalent_json() -> None:
    result = make_result(
        {
            "result.json": (
                '{\n'
                '  "items": [1, 2, 3],\n'
                '  "status": "ok"\n'
                "}"
            ),
        }
    )

    score = score_json_file(
        result,
        "result.json",
        {
            "status": "ok",
            "items": [1, 2, 3],
        },
    )

    assert score.passed is True
    assert score.score == 1.0


def test_score_json_file_rejects_invalid_json() -> None:
    result = make_result(
        {
            "result.json": '{"status":',
        }
    )

    score = score_json_file(
        result,
        "result.json",
        {
            "status": "ok",
        },
    )

    assert score.passed is False
    assert score.score == 0.0
    assert "result.json" in score.reason
    assert "invalid JSON" in score.reason


def test_score_json_file_reports_expected_value_mismatch() -> None:
    result = make_result(
        {
            "result.json": '{"status": "failed"}',
        }
    )

    score = score_json_file(
        result,
        "result.json",
        {
            "status": "ok",
        },
    )

    assert score.passed is False
    assert score.score == 0.0
    assert "result.json" in score.reason
    assert "mismatch" in score.reason


def test_score_json_file_reports_missing_file() -> None:
    result = make_result({})

    score = score_json_file(
        result,
        "result.json",
        {
            "status": "ok",
        },
    )

    assert score.passed is False
    assert score.score == 0.0
    assert "result.json" in score.reason
    assert "missing" in score.reason