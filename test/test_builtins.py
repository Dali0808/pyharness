from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.tools import ToolRegistry
from ai.schemas import ToolCallPart
from coding_agent.builtins import (
    create_read_file_tool,
    resolve_workspace_path,
)


def make_read_call(
    arguments: dict[str, object],
    call_id: str = "call-1",
) -> ToolCallPart:
    return ToolCallPart(
        id=call_id,
        name="read_file",
        arguments_json=json.dumps(arguments),
    )


def make_registry(workspace: Path) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(create_read_file_tool(workspace))
    return registry


def test_resolver_accepts_relative_path_inside_workspace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    nested = workspace / "notes"
    nested.mkdir(parents=True)
    expected = nested / "todo.txt"
    expected.write_text("test", encoding="utf-8")

    resolved = resolve_workspace_path(
        workspace,
        "notes/todo.txt",
    )

    assert resolved == expected.resolve()


def test_resolver_rejects_parent_segment(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(
        ValueError,
        match="parent path segments are not allowed",
    ):
        resolve_workspace_path(workspace, "notes/../todo.txt")


def test_resolver_rejects_absolute_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"

    with pytest.raises(
        ValueError,
        match="path must be relative to the workspace",
    ):
        resolve_workspace_path(workspace, str(outside.resolve()))


def test_resolver_rejects_symlink_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    link = workspace / "linked.txt"

    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symbolic links are unavailable: {exc}")

    with pytest.raises(
        ValueError,
        match="path resolves outside the workspace",
    ):
        resolve_workspace_path(workspace, "linked.txt")


@pytest.mark.asyncio
async def test_read_file_returns_utf8_content(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "message.txt").write_text(
        "你好，pyharness！",
        encoding="utf-8",
    )
    registry = make_registry(workspace)

    result = await registry.execute(
        make_read_call({"path": "message.txt"})
    )

    assert result.is_error is False
    assert result.tool_call_id == "call-1"
    assert result.tool_name == "read_file"
    assert result.content == "你好，pyharness！"


@pytest.mark.asyncio
async def test_read_file_missing_path_returns_tool_error(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    registry = make_registry(workspace)

    result = await registry.execute(
        make_read_call({"path": "missing.txt"})
    )

    assert result.is_error is True
    assert result.tool_call_id == "call-1"
    assert result.tool_name == "read_file"
    assert result.content.startswith("tool_execution_failed:")


@pytest.mark.asyncio
async def test_read_file_rejects_unknown_arguments(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    registry = make_registry(workspace)

    result = await registry.execute(
        make_read_call(
            {
                "path": "message.txt",
                "unexpected": True,
            }
        )
    )

    assert result.is_error is True
    assert result.tool_call_id == "call-1"
    assert result.tool_name == "read_file"
    assert result.content.startswith("invalid_arguments:")
    assert "unexpected" in result.content