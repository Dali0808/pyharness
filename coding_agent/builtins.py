from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agent.tools import AgentTool


class ReadFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    path: str = Field(
        min_length=1,
        description="Relative path to a UTF-8 text file in the workspace.",
    )


def resolve_workspace_path(
    workspace_root: Path,
    requested_path: str,
) -> Path:
    root = Path(workspace_root).resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError(f"workspace is not a directory: {root}")

    relative_path = Path(requested_path)

    # anchor also rejects drive-relative and rooted Windows paths such as
    # C:secret.txt and \Windows\system.ini.
    if relative_path.is_absolute() or relative_path.anchor:
        raise ValueError("path must be relative to the workspace")

    if ".." in relative_path.parts:
        raise ValueError("parent path segments are not allowed")

    resolved_path = (root / relative_path).resolve(strict=False)

    try:
        resolved_path.relative_to(root)
    except ValueError as exc:
        raise ValueError("path resolves outside the workspace") from exc

    return resolved_path


def create_read_file_tool(workspace_root: Path) -> AgentTool:
    root = Path(workspace_root).resolve(strict=True)

    if not root.is_dir():
        raise NotADirectoryError(f"workspace is not a directory: {root}")

    def read_file(arguments: BaseModel) -> str:
        if not isinstance(arguments, ReadFileArgs):
            raise TypeError("read_file received unexpected arguments")

        path = resolve_workspace_path(root, arguments.path)
        return path.read_text(encoding="utf-8")

    return AgentTool.from_handler(
        name="read_file",
        description="Read a UTF-8 text file inside the workspace.",
        args_model=ReadFileArgs,
        handler=read_file,
    )