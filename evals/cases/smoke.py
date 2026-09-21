from __future__ import annotations

from evals.cases.model import EvalCase


READ_FILE_CASE = EvalCase(
    case_id="workspace-read-file",
    task=(
        "Read notes/task.txt and report its exact contents "
        "in your final answer."
    ),
    initial_files={
        "notes/task.txt": (
            "Prepare a short project status summary.\n"
        ),
    },
    required_tools=frozenset({"read_file"}),
)


WRITE_FILE_CASE = EvalCase(
    case_id="workspace-write-file",
    task=(
        "Create output/greeting.txt with exactly this content:\n"
        "Hello from pyharness.\n"
    ),
    expected_files={
        "output/greeting.txt": "Hello from pyharness.\n",
    },
    required_tools=frozenset({"write_file"}),
)


LIST_DIR_CASE = EvalCase(
    case_id="workspace-list-dir",
    task=(
        "Inspect the src directory and report the names of "
        "the files it contains."
    ),
    initial_files={
        "src/main.py": "print('main')\n",
        "src/utils.py": "def helper():\n    return 1\n",
    },
    required_tools=frozenset({"list_dir"}),
)


READ_THEN_WRITE_CASE = EvalCase(
    case_id="workspace-read-then-write",
    task=(
        "Read input/requirements.txt and write its contents "
        "unchanged to output/copied-requirements.txt."
    ),
    initial_files={
        "input/requirements.txt": (
            "Python >= 3.11\n"
            "pytest\n"
        ),
    },
    expected_files={
        "output/copied-requirements.txt": (
            "Python >= 3.11\n"
            "pytest\n"
        ),
    },
    required_tools=frozenset(
        {
            "read_file",
            "write_file",
        }
    ),
)


TOOL_ERROR_RECOVERY_CASE = EvalCase(
    case_id="workspace-tool-error-recovery",
    task=(
        "The first path may be invalid. Recover from a failed "
        "file read, then read input/actual.txt and write its "
        "contents to output/recovered.txt."
    ),
    initial_files={
        "input/actual.txt": "Recovered successfully.\n",
    },
    expected_files={
        "output/recovered.txt": "Recovered successfully.\n",
    },
    required_tools=frozenset(
        {
            "read_file",
            "write_file",
        }
    ),
)


SMOKE_CASES: tuple[EvalCase, ...] = (
    READ_FILE_CASE,
    WRITE_FILE_CASE,
    LIST_DIR_CASE,
    READ_THEN_WRITE_CASE,
    TOOL_ERROR_RECOVERY_CASE,
)