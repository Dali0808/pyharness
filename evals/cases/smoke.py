from __future__ import annotations

from evals.cases.model import EvalCase


READ_FILE_CASE = EvalCase(
    case_id="workspace-read-file",
    task=(
        "Read notes/task.txt and write its contents unchanged "
        "to copied-task.txt."
    ),
    initial_files={
        "notes/task.txt": (
            "Prepare a short project status summary.\n"
        ),
    },
    expected_files={
        "copied-task.txt": (
            "Prepare a short project status summary.\n"
        ),
    },
    expected_tool_names=frozenset(
        {
            "read_file",
            "write_file",
        }
    ),
)


WRITE_FILE_CASE = EvalCase(
    case_id="workspace-write-file",
    task=(
        "Create greeting.txt with exactly this content:\n"
        "Hello from pyharness.\n"
    ),
    expected_files={
        "greeting.txt": "Hello from pyharness.\n",
    },
    expected_tool_names=frozenset({"write_file"}),
)


LIST_DIR_CASE = EvalCase(
    case_id="workspace-list-dir",
    task=(
        "List the src directory and write the file names in "
        "lexicographic order, one per line, to manifest.txt."
    ),
    initial_files={
        "src/main.py": "print('main')\n",
        "src/utils.py": "def helper():\n    return 1\n",
    },
    expected_files={
        "manifest.txt": "main.py\nutils.py\n",
    },
    expected_tool_names=frozenset(
        {
            "list_dir",
            "write_file",
        }
    ),
)


READ_THEN_WRITE_CASE = EvalCase(
    case_id="workspace-read-then-write",
    task=(
        "Read input/requirements.txt and write its contents "
        "unchanged to copied-requirements.txt."
    ),
    initial_files={
        "input/requirements.txt": (
            "Python >= 3.11\n"
            "pytest\n"
        ),
    },
    expected_files={
        "copied-requirements.txt": (
            "Python >= 3.11\n"
            "pytest\n"
        ),
    },
    expected_tool_names=frozenset(
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
        "contents to recovered.txt."
    ),
    initial_files={
        "input/actual.txt": "Recovered successfully.\n",
    },
    expected_files={
        "recovered.txt": "Recovered successfully.\n",
    },
    expected_tool_names=frozenset(
        {
            "read_file",
            "write_file",
        }
    ),
    requires_tool_error_recovery=True,
)


SMOKE_CASES: tuple[EvalCase, ...] = (
    READ_FILE_CASE,
    WRITE_FILE_CASE,
    LIST_DIR_CASE,
    READ_THEN_WRITE_CASE,
    TOOL_ERROR_RECOVERY_CASE,
)