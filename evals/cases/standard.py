from __future__ import annotations

from evals.cases.model import EvalCase


COPY_SINGLE_LINE_CASE = EvalCase(
    case_id="copy-single-line-file",
    task=(
        "Read source.txt and write its contents unchanged "
        "to copied.txt."
    ),
    initial_files={
        "source.txt": "One line of text.\n",
    },
    expected_files={
        "copied.txt": "One line of text.\n",
    },
    required_tools=frozenset(
        {
            "read_file",
            "write_file",
        }
    ),
)


COPY_MULTILINE_CASE = EvalCase(
    case_id="copy-multiline-file",
    task=(
        "Read notes.txt and write its contents unchanged "
        "to notes-copy.txt."
    ),
    initial_files={
        "notes.txt": (
            "First note.\n"
            "Second note.\n"
            "Third note.\n"
        ),
    },
    expected_files={
        "notes-copy.txt": (
            "First note.\n"
            "Second note.\n"
            "Third note.\n"
        ),
    },
    required_tools=frozenset(
        {
            "read_file",
            "write_file",
        }
    ),
)


COPY_UNICODE_CASE = EvalCase(
    case_id="copy-unicode-file",
    task=(
        "Read greeting.txt and write its contents unchanged "
        "to greeting-copy.txt."
    ),
    initial_files={
        "greeting.txt": "Hello, 世界!\n",
    },
    expected_files={
        "greeting-copy.txt": "Hello, 世界!\n",
    },
    required_tools=frozenset(
        {
            "read_file",
            "write_file",
        }
    ),
)


COPY_EMPTY_FILE_CASE = EvalCase(
    case_id="copy-empty-file",
    task=(
        "Read empty.txt and write its contents unchanged "
        "to empty-copy.txt."
    ),
    initial_files={
        "empty.txt": "",
    },
    expected_files={
        "empty-copy.txt": "",
    },
    required_tools=frozenset(
        {
            "read_file",
            "write_file",
        }
    ),
)


OVERWRITE_FILE_CASE = EvalCase(
    case_id="overwrite-existing-file",
    task=(
        "Replace the contents of draft.txt with exactly:\n"
        "Final version.\n"
    ),
    initial_files={
        "draft.txt": "Draft version.\n",
    },
    expected_files={
        "draft.txt": "Final version.\n",
    },
    required_tools=frozenset({"write_file"}),
)


CREATE_MULTILINE_FILE_CASE = EvalCase(
    case_id="create-multiline-file",
    task=(
        "Create release-notes.txt with exactly these lines:\n"
        "Version 1.0\n"
        "Added deterministic evals\n"
        "Fixed workspace isolation\n"
    ),
    expected_files={
        "release-notes.txt": (
            "Version 1.0\n"
            "Added deterministic evals\n"
            "Fixed workspace isolation\n"
        ),
    },
    required_tools=frozenset({"write_file"}),
)


COPY_NESTED_FILE_CASE = EvalCase(
    case_id="copy-nested-file",
    task=(
        "Read config/settings.txt and write its contents "
        "unchanged to settings-backup.txt."
    ),
    initial_files={
        "config/settings.txt": (
            "mode=testing\n"
            "retries=3\n"
        ),
    },
    expected_files={
        "settings-backup.txt": (
            "mode=testing\n"
            "retries=3\n"
        ),
    },
    required_tools=frozenset(
        {
            "read_file",
            "write_file",
        }
    ),
)


COMBINE_TWO_FILES_CASE = EvalCase(
    case_id="combine-two-files",
    task=(
        "Read first.txt and second.txt. Write their contents "
        "to combined.txt in that order with no extra text."
    ),
    initial_files={
        "first.txt": "First.\n",
        "second.txt": "Second.\n",
    },
    expected_files={
        "combined.txt": "First.\nSecond.\n",
    },
    required_tools=frozenset(
        {
            "read_file",
            "write_file",
        }
    ),
)


COMBINE_WITH_SEPARATOR_CASE = EvalCase(
    case_id="combine-files-with-separator",
    task=(
        "Read left.txt and right.txt. Write their contents "
        "to joined.txt with a line containing --- between them."
    ),
    initial_files={
        "left.txt": "Left side.\n",
        "right.txt": "Right side.\n",
    },
    expected_files={
        "joined.txt": (
            "Left side.\n"
            "---\n"
            "Right side.\n"
        ),
    },
    required_tools=frozenset(
        {
            "read_file",
            "write_file",
        }
    ),
)


COPY_CONFIG_BACKUP_CASE = EvalCase(
    case_id="copy-config-backup",
    task=(
        "Read app.conf and write its contents unchanged "
        "to app.conf.bak."
    ),
    initial_files={
        "app.conf": (
            "[server]\n"
            "port=8080\n"
            "debug=false\n"
        ),
    },
    expected_files={
        "app.conf.bak": (
            "[server]\n"
            "port=8080\n"
            "debug=false\n"
        ),
    },
    required_tools=frozenset(
        {
            "read_file",
            "write_file",
        }
    ),
)


LIST_ROOT_ENTRIES_CASE = EvalCase(
    case_id="list-root-entries",
    task=(
        "List the workspace root and write the entries in the "
        "tool output order to root-manifest.txt."
    ),
    initial_files={
        "alpha.txt": "alpha\n",
        "docs/guide.txt": "guide\n",
    },
    expected_files={
        "root-manifest.txt": (
            "file: alpha.txt\n"
            "dir: docs\n"
        ),
    },
    required_tools=frozenset(
        {
            "list_dir",
            "write_file",
        }
    ),
)


LIST_NESTED_ENTRIES_CASE = EvalCase(
    case_id="list-nested-entries",
    task=(
        "List the assets directory and write the entries in "
        "the tool output order to assets-manifest.txt."
    ),
    initial_files={
        "assets/one.txt": "one\n",
        "assets/two.txt": "two\n",
    },
    expected_files={
        "assets-manifest.txt": (
            "file: one.txt\n"
            "file: two.txt\n"
        ),
    },
    required_tools=frozenset(
        {
            "list_dir",
            "write_file",
        }
    ),
)


LIST_DIRECTORY_WITH_CHILD_CASE = EvalCase(
    case_id="list-directory-with-child",
    task=(
        "List the project directory and write the entries in "
        "the tool output order to project-manifest.txt."
    ),
    initial_files={
        "project/README.md": "Project readme.\n",
        "project/src/main.py": "print('main')\n",
    },
    expected_files={
        "project-manifest.txt": (
            "file: README.md\n"
            "dir: src\n"
        ),
    },
    required_tools=frozenset(
        {
            "list_dir",
            "write_file",
        }
    ),
)


LIST_DOCUMENT_ENTRIES_CASE = EvalCase(
    case_id="list-document-entries",
    task=(
        "List the documents directory and write the entries "
        "in the tool output order to documents-manifest.txt."
    ),
    initial_files={
        "documents/plan.md": "Plan.\n",
        "documents/todo.md": "Todo.\n",
        "documents/archive/old.md": "Old.\n",
    },
    expected_files={
        "documents-manifest.txt": (
            "dir: archive\n"
            "file: plan.md\n"
            "file: todo.md\n"
        ),
    },
    required_tools=frozenset(
        {
            "list_dir",
            "write_file",
        }
    ),
)


TOOL_ERROR_RECOVERY_MISSING_FILE_CASE = EvalCase(
    case_id="tool-error-recovery-missing-file",
    task=(
        "The first attempt to read missing.txt may fail. "
        "Recover by reading source.txt and writing its contents "
        "unchanged to recovered-copy.txt."
    ),
    initial_files={
        "source.txt": "Recovered from missing file.\n",
    },
    expected_files={
        "recovered-copy.txt": (
            "Recovered from missing file.\n"
        ),
    },
    required_tools=frozenset(
        {
            "read_file",
            "write_file",
        }
    ),
    requires_tool_error_recovery=True,
)


STANDARD_CASES: tuple[EvalCase, ...] = (
    COPY_SINGLE_LINE_CASE,
    COPY_MULTILINE_CASE,
    COPY_UNICODE_CASE,
    COPY_EMPTY_FILE_CASE,
    OVERWRITE_FILE_CASE,
    CREATE_MULTILINE_FILE_CASE,
    COPY_NESTED_FILE_CASE,
    COMBINE_TWO_FILES_CASE,
    COMBINE_WITH_SEPARATOR_CASE,
    COPY_CONFIG_BACKUP_CASE,
    LIST_ROOT_ENTRIES_CASE,
    LIST_NESTED_ENTRIES_CASE,
    LIST_DIRECTORY_WITH_CHILD_CASE,
    LIST_DOCUMENT_ENTRIES_CASE,
    TOOL_ERROR_RECOVERY_MISSING_FILE_CASE,
)