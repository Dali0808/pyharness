from __future__ import annotations

import difflib
import fnmatch
import inspect
import os
import stat
import tempfile
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agent.tools import AgentTool

MAX_FILE_BYTES = 1_000_000
MAX_RESULT_BYTES = 32_000
MAX_MATCHES = 100
MAX_SCANNED_FILES = 10_000
MAX_READ_LINES = 500

Approval = Callable[[str], bool | Awaitable[bool]]


class ReadFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    path: str = Field(min_length=1)
    offset: int = Field(default=1, ge=1)
    limit: int | None = Field(
        default=None,
        ge=1,
        le=MAX_READ_LINES,
    )


class WriteFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    path: str = Field(min_length=1)
    content: str


class ListDirArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(min_length=1)


class GlobFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    pattern: str = Field(min_length=1, max_length=200)
    path: str = Field(default=".", min_length=1)


class GrepFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    pattern: str = Field(min_length=1, max_length=200)
    path: str = Field(default=".", min_length=1)
    glob: str | None = Field(default=None, max_length=100)


class EditFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(min_length=1)
    old_string: str = Field(min_length=1)
    new_string: str


def resolve_workspace_path(workspace_root: Path, requested_path: str) -> Path:
    root = Path(workspace_root).resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError(f"workspace is not a directory: {root}")

    relative_path = Path(requested_path)
    # anchor also rejects drive-relative and rooted Windows paths.
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


def _clip(text: str) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= MAX_RESULT_BYTES:
        return text
    suffix = b"\n[output truncated]"
    body = encoded[:MAX_RESULT_BYTES - len(suffix)]
    return body.decode("utf-8", errors="ignore") + suffix.decode()


def _workspace_files(root: Path, requested_path: str) -> Iterator[Path]:
    base = resolve_workspace_path(root, requested_path)
    if base.is_file():
        yield base
        return
    if not base.is_dir():
        raise NotADirectoryError(requested_path)

    for directory, dirs, files in os.walk(base, followlinks=False):
        dirs[:] = sorted(
            name
            for name in dirs
            if name not in {".git", ".venv"}
            and not (Path(directory) / name).is_symlink()
        )
        for name in sorted(files):
            candidate = Path(directory) / name
            try:
                path = resolve_workspace_path(
                    root, str(candidate.relative_to(root))
                )
            except ValueError:
                continue
            if path.is_file():
                yield path


def create_read_file_tool(workspace_root: Path) -> AgentTool:
    root = Path(workspace_root).resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError(f"workspace is not a directory: {root}")

    def read_file(arguments: BaseModel) -> str:
        if not isinstance(arguments, ReadFileArgs):
            raise TypeError("read_file received unexpected arguments")
        path = resolve_workspace_path(root, arguments.path)
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("file exceeds the read limit")
        content = path.read_text(encoding="utf-8")
        if arguments.limit is None and arguments.offset == 1:
            return _clip(content)
        lines = content.splitlines()
        if arguments.offset > len(lines):
            raise ValueError(f"offset exceeds file length ({len(lines)} lines)")
        limit = arguments.limit or MAX_READ_LINES
        selected = lines[arguments.offset - 1:arguments.offset - 1 + limit]
        return _clip("\n".join(
            f"{number}: {line}"
            for number, line in enumerate(selected, arguments.offset)
        ))

    return AgentTool.from_handler(
        name="read_file",
        description="read",
        args_model=ReadFileArgs,
        handler=read_file,
    )


async def _approved(approval: Approval, diff: str) -> bool:
    result = approval(diff)
    if inspect.isawaitable(result):
        result = await result
    return bool(result)


async def _apply_approved_change(
    path: Path,
    before: bytes | None,
    new_content: str,
    approval: Approval,
) -> None:
    after = new_content.encode("utf-8")
    if len(after) > MAX_FILE_BYTES:
        raise ValueError("file exceeds the edit limit")
    if before == after:
        return

    old_text = before.decode("utf-8") if before is not None else ""
    diff = "".join(difflib.unified_diff(
        old_text.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=f"a/{path.name}",
        tofile=f"b/{path.name}",
    ))
    if len(diff.encode("utf-8")) > MAX_RESULT_BYTES:
        raise ValueError("diff exceeds the review limit")
    if not await _approved(approval, diff):
        raise PermissionError("change was rejected")

    current = path.read_bytes() if path.exists() else None
    if current != before:
        raise ValueError("file changed while awaiting approval")

    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as temporary:
        temporary.write(after)
        temporary_path = Path(temporary.name)
    try:
        if mode is not None:
            temporary_path.chmod(mode)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def create_write_file_tool(
    workspace_root: Path,
    approval: Approval | None = None,
) -> AgentTool:
    root = Path(workspace_root).resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError(f"workspace is not a directory: {root}")

    async def write_file(arguments: BaseModel) -> str:
        if not isinstance(arguments, WriteFileArgs):
            raise TypeError("write_file received unexpected arguments")
        path = resolve_workspace_path(root, arguments.path)
        if approval is None:
            path.write_text(arguments.content, encoding="utf-8")
        else:
            before = path.read_bytes() if path.exists() else None
            await _apply_approved_change(path, before, arguments.content, approval)
        return f"wrote file: {arguments.path}"

    return AgentTool.from_handler(
        name="write_file",
        description="write",
        args_model=WriteFileArgs,
        handler=write_file,
    )


def create_edit_file_tool(workspace_root: Path, approval: Approval) -> AgentTool:
    root = Path(workspace_root).resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError(f"workspace is not a directory: {root}")

    async def edit_file(arguments: BaseModel) -> str:
        if not isinstance(arguments, EditFileArgs):
            raise TypeError("edit_file received unexpected arguments")
        path = resolve_workspace_path(root, arguments.path)
        before = path.read_bytes()
        if len(before) > MAX_FILE_BYTES:
            raise ValueError("file exceeds the edit limit")
        content = before.decode("utf-8")
        if content.count(arguments.old_string) != 1:
            raise ValueError("old_string must match exactly once")
        await _apply_approved_change(
            path,
            before,
            content.replace(arguments.old_string, arguments.new_string, 1),
            approval,
        )
        return f"edited file: {arguments.path}"

    return AgentTool.from_handler(
        name="edit_file",
        description="edit",
        args_model=EditFileArgs,
        handler=edit_file,
    )


def create_glob_file_tool(workspace_root: Path) -> AgentTool:
    root = Path(workspace_root).resolve(strict=True)

    def glob_file(arguments: BaseModel) -> str:
        if not isinstance(arguments, GlobFileArgs):
            raise TypeError("glob_file received unexpected arguments")
        matches: list[str] = []
        for index, path in enumerate(_workspace_files(root, arguments.path)):
            if index >= MAX_SCANNED_FILES or len(matches) >= MAX_MATCHES:
                break
            relative = path.relative_to(root).as_posix()
            target = relative if "/" in arguments.pattern else path.name
            if fnmatch.fnmatch(target, arguments.pattern):
                matches.append(relative)
        return _clip("\n".join(matches) or "no matches")

    return AgentTool.from_handler(
        name="glob_file",
        description="glob",
        args_model=GlobFileArgs,
        handler=glob_file,
    )


def create_grep_file_tool(workspace_root: Path) -> AgentTool:
    root = Path(workspace_root).resolve(strict=True)

    def grep_file(arguments: BaseModel) -> str:
        if not isinstance(arguments, GrepFileArgs):
            raise TypeError("grep_file received unexpected arguments")
        hits: list[str] = []
        for index, path in enumerate(_workspace_files(root, arguments.path)):
            if index >= MAX_SCANNED_FILES or len(hits) >= MAX_MATCHES:
                break
            if arguments.glob and not fnmatch.fnmatch(path.name, arguments.glob):
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
                for number, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(), 1
                ):
                    if arguments.pattern in line:
                        hits.append(
                            f"{path.relative_to(root)}:{number}:{line[:200]}"
                        )
                        if len(hits) >= MAX_MATCHES:
                            break
            except (OSError, UnicodeError):
                continue
        return _clip("\n".join(hits) or "no matches")

    return AgentTool.from_handler(
        name="grep_file",
        description="grep",
        args_model=GrepFileArgs,
        handler=grep_file,
    )


def create_list_dir_tool(workspace_root: Path) -> AgentTool:
    root = Path(workspace_root).resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError(f"workspace is not a directory: {root}")

    def list_dir(arguments: BaseModel) -> str:
        if not isinstance(arguments, ListDirArgs):
            raise TypeError("list_dir received unexpected arguments")
        path = resolve_workspace_path(root, arguments.path)
        entries = sorted(path.iterdir(), key=lambda entry: entry.name)
        return "\n".join(
            f"{'dir' if entry.is_dir() else 'file'}: {entry.name}"
            for entry in entries
        )

    return AgentTool.from_handler(
        name="list_dir",
        description="list",
        args_model=ListDirArgs,
        handler=list_dir,
    )
