from __future__ import annotations

from agent.loop import RunFailure, RunResult
from ai.schemas import ToolResultMessage, Usage
from coding_agent.cli import TaskRunResult
from evals.runner import EvalRunResult
from evals.cases.model import EvalCase
from evals.scorers import (
    ScoreResult,
    score_case,
    score_file_contents,
    score_file_exists,
    score_json_file,
    score_tool_error_recovery,
)


def make_result(
    files: dict[str, str],
    *,
    exit_code: int = 0,
    tool_errors: tuple[ToolResultMessage, ...] = (),
) -> EvalRunResult:
    if exit_code == 0:
        run_result = RunResult(
            final_message=None,
            failure=None,
            steps=1,
            usage=Usage(),
        )
    else:
        run_result = RunResult(
            final_message=None,
            failure=RunFailure(
                code="provider_error",
                message="evaluation run failed",
            ),
            steps=1,
            usage=Usage(),
        )
    return EvalRunResult(
        case_id="scorer-test",
        task_result=TaskRunResult(
            exit_code=exit_code,
            run_result=run_result,
        ),
        elapsed_seconds=0.01,
        final_files=files,
        tool_errors=tool_errors,
    )


def tool_error(
    tool_name: str = "read_file",
) -> ToolResultMessage:
    return ToolResultMessage(
        tool_call_id="call-error",
        tool_name=tool_name,
        content="tool_execution_failed: test error",
        is_error=True,
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


def test_score_tool_error_recovery_passes() -> None:
    result = make_result(
        {
            "recovered.txt": "Recovered successfully.\n",
        },
        tool_errors=(tool_error(),),
    )

    score = score_tool_error_recovery(
        result,
        "recovered.txt",
        "Recovered successfully.\n",
    )

    assert score.passed is True
    assert score.score == 1.0


def test_score_tool_error_recovery_fails_without_tool_error() -> None:
    result = make_result(
        {
            "recovered.txt": "Recovered successfully.\n",
        },
    )

    score = score_tool_error_recovery(
        result,
        "recovered.txt",
        "Recovered successfully.\n",
    )

    assert score.passed is False
    assert score.score == 0.0
    assert "tool error" in score.reason


def test_score_tool_error_recovery_fails_when_run_failed() -> None:
    result = make_result(
        {
            "recovered.txt": "Recovered successfully.\n",
        },
        exit_code=1,
        tool_errors=(tool_error(),),
    )

    score = score_tool_error_recovery(
        result,
        "recovered.txt",
        "Recovered successfully.\n",
    )

    assert score.passed is False
    assert score.score == 0.0
    assert "run failed" in score.reason


def test_score_tool_error_recovery_fails_for_wrong_output() -> None:
    result = make_result(
        {
            "recovered.txt": "wrong content",
        },
        tool_errors=(tool_error(),),
    )

    score = score_tool_error_recovery(
        result,
        "recovered.txt",
        "Recovered successfully.\n",
    )

    assert score.passed is False
    assert score.score == 0.0
    assert "mismatch" in score.reason


def test_score_case_uses_file_content_scoring() -> None:
    case = EvalCase(
        case_id="content-case",
        task="Create result.txt.",
        expected_files={
            "result.txt": "expected content",
        },
        required_tools=frozenset({"write_file"}),
    )
    result = make_result(
        {
            "result.txt": "expected content",
        }
    )

    score = score_case(case, result)

    assert score == ScoreResult(
        passed=True,
        score=1.0,
        reason="all expected file contents match",
    )


def test_score_case_uses_error_recovery_scoring() -> None:
    case = EvalCase(
        case_id="recovery-case",
        task="Recover from a failed tool call.",
        expected_files={
            "recovered.txt": "Recovered.\n",
        },
        required_tools=frozenset(
            {
                "read_file",
                "write_file",
            }
        ),
        requires_tool_error_recovery=True,
    )
    result = make_result(
        {
            "recovered.txt": "Recovered.\n",
        },
        tool_errors=(tool_error(),),
    )

    score = score_case(case, result)

    assert score.passed is True
    assert score.score == 1.0


def test_score_case_rejects_missing_expectations() -> None:
    case = EvalCase(
        case_id="unscorable-case",
        task="Do something.",
        required_tools=frozenset({"read_file"}),
    )
    result = make_result({})

    score = score_case(case, result)

    assert score.passed is False
    assert score.score == 0.0
    assert "no deterministic expected files" in score.reason


def test_score_case_rejects_recovery_case_with_multiple_outputs() -> None:
    case = EvalCase(
        case_id="invalid-recovery-case",
        task="Recover from an error.",
        expected_files={
            "first.txt": "first",
            "second.txt": "second",
        },
        required_tools=frozenset(
            {
                "read_file",
                "write_file",
            }
        ),
        requires_tool_error_recovery=True,
    )
    result = make_result(
        {
            "first.txt": "first",
            "second.txt": "second",
        },
        tool_errors=(tool_error(),),
    )

    score = score_case(case, result)

    assert score.passed is False
    assert score.score == 0.0
    assert "exactly one expected file" in score.reason