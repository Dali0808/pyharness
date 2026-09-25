from __future__ import annotations

from pathlib import Path

import pytest

from evals.cases.model import EvalCase, EvalCaseError
from evals.cases.smoke import SMOKE_CASES


def test_smoke_cases_have_unique_ids() -> None:
    case_ids = [
        case.case_id
        for case in SMOKE_CASES
    ]

    assert len(case_ids) == len(set(case_ids))


def test_smoke_cases_have_non_empty_tasks() -> None:
    assert SMOKE_CASES

    for case in SMOKE_CASES:
        assert case.case_id.strip()
        assert case.task.strip()
        assert case.required_tools


def test_case_rejects_absolute_seed_path() -> None:
    with pytest.raises(
        EvalCaseError,
        match="path must be relative",
    ):
        EvalCase(
            case_id="invalid-absolute-path",
            task="Use a file.",
            initial_files={
                str(Path("/tmp/outside.txt")): "secret",
            },
            required_tools=frozenset({"read_file"}),
        )


def test_case_rejects_parent_seed_path() -> None:
    with pytest.raises(
        EvalCaseError,
        match="must not contain '..'",
    ):
        EvalCase(
            case_id="invalid-parent-path",
            task="Use a file.",
            initial_files={
                "../outside.txt": "secret",
            },
            required_tools=frozenset({"read_file"}),
        )


def test_case_rejects_unsafe_expected_path() -> None:
    with pytest.raises(
        EvalCaseError,
        match="must not contain '..'",
    ):
        EvalCase(
            case_id="invalid-expected-path",
            task="Create a file.",
            expected_files={
                "output/../outside.txt": "secret",
            },
            required_tools=frozenset({"write_file"}),
        )


def test_case_preserves_seed_file_mapping() -> None:
    initial_files = {
        "input.txt": "original",
    }

    case = EvalCase(
        case_id="immutable-input",
        task="Read the input.",
        initial_files=initial_files,
        required_tools=frozenset({"read_file"}),
    )

    initial_files["input.txt"] = "changed"

    assert case.initial_files["input.txt"] == "original"

    with pytest.raises(TypeError):
        case.initial_files["another.txt"] = "not allowed"  # type: ignore[index]


def test_case_rejects_empty_case_id() -> None:
    with pytest.raises(
        EvalCaseError,
        match="case_id must not be empty",
    ):
        EvalCase(
            case_id=" ",
            task="Do something.",
            required_tools=frozenset({"read_file"}),
        )


def test_case_rejects_empty_task() -> None:
    with pytest.raises(
        EvalCaseError,
        match="task must not be empty",
    ):
        EvalCase(
            case_id="empty-task",
            task=" ",
            required_tools=frozenset({"read_file"}),
        )


def test_case_rejects_non_boolean_recovery_requirement() -> None:
    with pytest.raises(
        EvalCaseError,
        match="must be a boolean",
    ):
        EvalCase(
            case_id="invalid-recovery-requirement",
            task="Recover from an error.",
            required_tools=frozenset({"read_file"}),
            requires_tool_error_recovery="yes",  # type: ignore[arg-type]
        )


def test_smoke_catalog_covers_workspace_tool_workflows() -> None:
    required_tools = {
        "read_file",
        "write_file",
        "list_dir",
    }
    covered_tools = set().union(
        *(case.required_tools for case in SMOKE_CASES)
    )

    assert required_tools <= covered_tools


def test_smoke_catalog_contains_file_expectations() -> None:
    assert SMOKE_CASES

    for case in SMOKE_CASES:
        assert case.expected_files


def test_smoke_catalog_marks_one_error_recovery_case() -> None:
    recovery_cases = [
        case
        for case in SMOKE_CASES
        if case.requires_tool_error_recovery
    ]

    assert [case.case_id for case in recovery_cases] == [
        "workspace-tool-error-recovery",
    ]