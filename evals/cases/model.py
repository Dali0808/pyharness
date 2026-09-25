from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from types import MappingProxyType
from typing import Mapping


class EvalCaseError(ValueError):
    """Raised when an evaluation case violates its data contract."""


@dataclass(frozen=True, slots=True)
class EvalCase:
    case_id: str
    task: str
    initial_files: Mapping[str, str] = field(
        default_factory=dict,
    )
    expected_files: Mapping[str, str] = field(
        default_factory=dict,
    )
    required_tools: frozenset[str] = frozenset()
    requires_tool_error_recovery: bool = False

    def __post_init__(self) -> None:
        if not self.case_id.strip():
            raise EvalCaseError("case_id must not be empty")

        if not self.task.strip():
            raise EvalCaseError("task must not be empty")

        if not self.required_tools:
            raise EvalCaseError(
                "required_tools must not be empty"
            )

        if any(
            not tool_name.strip()
            for tool_name in self.required_tools
        ):
            raise EvalCaseError(
                "required tool names must not be empty"
            )

        if not isinstance(
            self.requires_tool_error_recovery,
            bool,
        ):
            raise EvalCaseError(
                "requires_tool_error_recovery must be a boolean"
            )

        initial_files = self._validate_files(
            self.initial_files,
            field_name="initial_files",
        )
        expected_files = self._validate_files(
            self.expected_files,
            field_name="expected_files",
        )

        object.__setattr__(
            self,
            "initial_files",
            MappingProxyType(initial_files),
        )
        object.__setattr__(
            self,
            "expected_files",
            MappingProxyType(expected_files),
        )

    @staticmethod
    def _validate_files(
        files: Mapping[str, str],
        *,
        field_name: str,
    ) -> dict[str, str]:
        if not isinstance(files, Mapping):
            raise EvalCaseError(
                f"{field_name} must be a mapping"
            )

        validated: dict[str, str] = {}

        for path_text, content in files.items():
            if not isinstance(path_text, str):
                raise EvalCaseError(
                    f"{field_name} paths must be strings"
                )

            if not isinstance(content, str):
                raise EvalCaseError(
                    f"{field_name} contents must be strings"
                )

            EvalCase._validate_relative_path(
                path_text,
                field_name=field_name,
            )
            validated[path_text] = content

        return validated

    @staticmethod
    def _validate_relative_path(
        path_text: str,
        *,
        field_name: str,
    ) -> None:
        if not path_text:
            raise EvalCaseError(
                f"{field_name} contains an empty path"
            )

        if "\x00" in path_text:
            raise EvalCaseError(
                f"{field_name} contains a null byte"
            )

        posix_path = Path(path_text)
        windows_path = PureWindowsPath(path_text)

        if (
            posix_path.is_absolute()
            or windows_path.is_absolute()
            or windows_path.drive
            or windows_path.root
        ):
            raise EvalCaseError(
                f"{field_name} path must be relative: "
                f"{path_text}"
            )

        if (
            ".." in posix_path.parts
            or ".." in windows_path.parts
        ):
            raise EvalCaseError(
                f"{field_name} path must not contain '..': "
                f"{path_text}"
            )