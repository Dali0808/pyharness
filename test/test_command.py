from __future__ import annotations

import asyncio
import json
import os
import platform
import sys
from pathlib import Path

import pytest

from ai.schemas import ToolCallPart
from agent.tools import ToolRegistry
from coding_agent.command import create_run_command_tool


def command_call(argv: list[str], cwd: str = ".") -> ToolCallPart:
    return ToolCallPart(
        id="command-1",
        name="run_command",
        arguments_json=json.dumps({"argv": argv, "cwd": cwd}),
    )


def registry(workspace: Path, approve) -> ToolRegistry:
    tools = ToolRegistry()
    tools.register(create_run_command_tool(workspace, approve))
    return tools


@pytest.mark.asyncio
async def test_command_rejects_without_starting_process(tmp_path: Path) -> None:
    result = await registry(tmp_path, lambda argv, cwd: False).execute(
        command_call(["/bin/touch", "created.txt"])
    )

    assert result.is_error
    assert "rejected" in result.content
    assert not (tmp_path / "created.txt").exists()


@pytest.mark.asyncio
async def test_command_rejects_outside_working_directory(tmp_path: Path) -> None:
    result = await registry(tmp_path, lambda argv, cwd: True).execute(
        command_call(["/bin/pwd"], "..")
    )

    assert result.is_error
    assert "parent path segments" in result.content


@pytest.mark.asyncio
async def test_command_fails_closed_without_sandbox(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("coding_agent.command.platform.system", lambda: "Linux")
    result = await registry(tmp_path, lambda argv, cwd: True).execute(
        command_call(["/bin/touch", "created.txt"])
    )

    assert result.is_error
    assert "sandbox is unavailable" in result.content
    assert not (tmp_path / "created.txt").exists()


@pytest.mark.skipif(
    platform.system() != "Darwin" or not Path("/usr/bin/sandbox-exec").is_file(),
    reason="requires macOS sandbox-exec",
)
@pytest.mark.asyncio
async def test_command_runs_in_sandbox_and_blocks_outside_read(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "inside.txt").write_text("visible", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    approved: list[tuple[list[str], Path]] = []

    def approve(argv: list[str], cwd: Path) -> bool:
        approved.append((argv, cwd))
        return True

    tools = registry(workspace, approve)
    success = await tools.execute(command_call(["/bin/cat", "inside.txt"]))
    python = await tools.execute(command_call([sys.executable, "-c", "print('python-ok')"]))
    blocked = await tools.execute(command_call(["/bin/cat", str(outside)]))
    network = await tools.execute(command_call([
        sys.executable, "-c",
        "import socket; socket.socket().bind(('127.0.0.1', 0))",
    ]))

    assert not success.is_error
    assert "exit_code: 0" in success.content
    assert "visible" in success.content
    assert "exit_code: 0" in python.content
    assert "python-ok" in python.content
    assert not blocked.is_error
    assert "exit_code: 1" in blocked.content
    assert "secret" not in blocked.content
    assert "exit_code: 1" in network.content
    assert "Operation not permitted" in network.content
    assert approved == [
        (["/bin/cat", "inside.txt"], workspace),
        ([sys.executable, "-c", "print('python-ok')"], workspace),
        (["/bin/cat", str(outside)], workspace),
        ([sys.executable, "-c", "import socket; socket.socket().bind(('127.0.0.1', 0))"], workspace),
    ]


@pytest.mark.skipif(
    platform.system() != "Darwin" or not Path("/usr/bin/sandbox-exec").is_file(),
    reason="requires macOS sandbox-exec",
)
@pytest.mark.asyncio
async def test_command_timeout_terminates_process(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("coding_agent.command.MAX_SECONDS", 0.1)
    result = await registry(tmp_path, lambda argv, cwd: True).execute(
        command_call(["/bin/sleep", "10"])
    )

    assert "timed out" in result.content
    assert "exit_code:" in result.content


@pytest.mark.skipif(
    platform.system() != "Darwin" or not Path("/usr/bin/sandbox-exec").is_file(),
    reason="requires macOS sandbox-exec",
)
@pytest.mark.asyncio
async def test_command_limits_output(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("coding_agent.command.MAX_OUTPUT_BYTES", 64)
    result = await registry(tmp_path, lambda argv, cwd: True).execute(
        command_call(["/usr/bin/yes"])
    )

    assert "output exceeded 64 bytes" in result.content
    assert len(result.content) < 200


@pytest.mark.skipif(
    platform.system() != "Darwin" or not Path("/usr/bin/sandbox-exec").is_file(),
    reason="requires macOS sandbox-exec",
)
@pytest.mark.asyncio
async def test_command_cancellation_stops_process(tmp_path: Path, monkeypatch) -> None:
    started = asyncio.Event()
    process_ids: list[int] = []
    original_spawn = asyncio.create_subprocess_exec

    async def capture_spawn(*args, **kwargs):
        process = await original_spawn(*args, **kwargs)
        process_ids.append(process.pid)
        return process

    monkeypatch.setattr("coding_agent.command.asyncio.create_subprocess_exec", capture_spawn)

    def approve(argv: list[str], cwd: Path) -> bool:
        started.set()
        return True

    task = asyncio.create_task(registry(tmp_path, approve).execute(
        command_call(["/bin/sleep", "10"])
    ))
    await started.wait()
    await asyncio.sleep(0.1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(process_ids) == 1
    with pytest.raises(ProcessLookupError):
        os.killpg(process_ids[0], 0)
