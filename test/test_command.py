from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from ai.schemas import ToolCallPart
from agent.tools import ToolRegistry
from coding_agent import command
from coding_agent.command import create_run_command_tool


def command_call(argv: list[str], cwd: str = ".") -> ToolCallPart:
    return ToolCallPart(
        id="command-1", name="run_command",
        arguments_json=json.dumps({"argv": argv, "cwd": cwd}),
    )


def registry(workspace: Path, approve) -> ToolRegistry:
    tools = ToolRegistry()
    tools.register(create_run_command_tool(workspace, approve))
    return tools


@pytest.mark.asyncio
async def test_command_rejects_without_starting_process(tmp_path, fake_docker):
    result = await registry(tmp_path, lambda argv, cwd: False).execute(
        command_call(["touch", "created.txt"])
    )
    assert result.is_error
    assert "rejected" in result.content
    assert fake_docker.calls == []
    assert not (tmp_path / "created.txt").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("cwd", ["..", "sub/../", "/outside"])
async def test_command_rejects_outside_working_directory(tmp_path, fake_docker, cwd):
    result = await registry(tmp_path, lambda argv, cwd: True).execute(command_call(["pwd"], cwd))
    assert result.is_error
    assert fake_docker.calls == []


@pytest.mark.asyncio
async def test_command_fails_closed_without_docker(tmp_path, monkeypatch):
    monkeypatch.setattr(command.shutil, "which", lambda _: None)
    result = await registry(tmp_path, lambda argv, cwd: True).execute(command_call(["touch", "created.txt"]))
    assert result.is_error
    assert "Docker sandbox is unavailable" in result.content
    assert not (tmp_path / "created.txt").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [b"Cannot connect to the Docker daemon", b"No such image"])
async def test_command_fails_closed_on_docker_setup_error(tmp_path, fake_docker, error):
    fake_docker.create_error = error
    result = await registry(tmp_path, lambda argv, cwd: True).execute(command_call(["python", "-V"]))
    assert result.is_error
    assert error.decode() in result.content
    assert [call[0] for call in fake_docker.calls] == ["create", "rm"]
    assert not fake_docker.snapshot.exists()


@pytest.mark.asyncio
async def test_command_uses_isolated_snapshot_and_literal_arguments(tmp_path, fake_docker, monkeypatch):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "source.py").write_text("original", encoding="utf-8")
    for name in (".git", ".runtime", ".venv"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "secret").write_text("private")
    (tmp_path / ".env").write_text("API_KEY=private")
    monkeypatch.setenv("TEST_MODEL_KEY", "must-not-enter-container")
    approved = []

    async def approve(argv, cwd):
        approved.append((argv, cwd))
        return True

    argv = ["python", "-c", "import sys; print(sys.argv[1])", "x; touch host-file"]
    result = await registry(tmp_path, approve).execute(command_call(argv, "sub"))
    assert not result.is_error
    assert approved == [(argv, (tmp_path / "sub").resolve())]
    create = fake_docker.calls[0]
    assert json.loads(create[-2]) == argv
    assert create[-1] == "/workspace/sub"
    assert fake_docker.files == {"sub/source.py": b"original"}
    assert fake_docker.snapshot != tmp_path
    mount = create[create.index("--mount") + 1]
    assert "target=/input,readonly,bind-recursive=disabled" in mount
    for option, value in (
        ("--network", "none"), ("--user", "65534:65534"),
        ("--cap-drop", "ALL"), ("--security-opt", "no-new-privileges"),
        ("--memory", "512m"), ("--memory-swap", "512m"),
        ("--cpus", "1"), ("--pids-limit", "64"), ("--pull", "never"),
        ("--log-driver", "none"),
    ):
        assert create[create.index(option) + 1] == value
    assert "--read-only" in create
    assert "/workspace:rw,nosuid,nodev,size=256m,mode=0700,uid=65534,gid=65534" in create
    assert "--privileged" not in create
    assert "TEST_MODEL_KEY" not in str(create)
    assert "must-not-enter-container" not in str(create)
    assert (tmp_path / "sub" / "source.py").read_text() == "original"
    assert not fake_docker.snapshot.exists()
    assert [call[0] for call in fake_docker.calls] == ["create", "start", "inspect", "rm"]


@pytest.mark.asyncio
async def test_command_snapshot_preserves_binary_file_bytes(tmp_path, fake_docker):
    original = b"first\r\nsecond\x1a\x00\xff"
    (tmp_path / "binary.dat").write_bytes(original)
    result = await registry(tmp_path, lambda argv, cwd: True).execute(command_call(["python", "-V"]))
    assert not result.is_error
    assert fake_docker.files["binary.dat"] == original


@pytest.mark.asyncio
async def test_command_returns_container_exit_code(tmp_path, fake_docker):
    fake_docker.exit_code = 17
    result = await registry(tmp_path, lambda argv, cwd: True).execute(command_call(["python", "-V"]))
    assert not result.is_error
    assert "exit_code: 17" in result.content


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [
    b'{"Running": true, "ExitCode": 0}',
    b'{"Running": false, "ExitCode": 0, "Error": "start failed"}',
    b'{"Running": false, "ExitCode": 0, "StartedAt": "0001-01-01T00:00:00Z"}',
])
async def test_command_does_not_report_success_without_valid_terminal_state(tmp_path, fake_docker, state):
    fake_docker.state = state
    result = await registry(tmp_path, lambda argv, cwd: True).execute(command_call(["python", "-V"]))
    assert result.is_error
    assert fake_docker.calls[-1][0] == "rm"


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", ["MAX_SNAPSHOT_BYTES", "MAX_SNAPSHOT_ENTRIES"])
async def test_command_bounds_snapshot_before_starting_docker(tmp_path, fake_docker, monkeypatch, limit):
    (tmp_path / "one.txt").write_bytes(b"1234")
    (tmp_path / "two.txt").write_bytes(b"5678")
    monkeypatch.setattr(command, limit, 1)
    result = await registry(tmp_path, lambda argv, cwd: True).execute(command_call(["python", "-V"]))
    assert result.is_error
    assert "snapshot exceeds" in result.content
    assert fake_docker.calls == []


@pytest.mark.asyncio
async def test_command_rejects_snapshot_symlink(tmp_path, fake_docker):
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.write_text("secret")
    try:
        (tmp_path / "link").symlink_to(outside)
    except OSError:
        pytest.skip("Creating symbolic links is not permitted on this host")
    result = await registry(tmp_path, lambda argv, cwd: True).execute(command_call(["cat", "link"]))
    assert result.is_error
    assert "symbolic links" in result.content
    assert fake_docker.calls == []


@pytest.mark.asyncio
async def test_command_timeout_removes_container_and_keeps_partial_output(tmp_path, fake_docker, monkeypatch):
    fake_docker.block_start = True
    fake_docker.output = b"started\n"
    monkeypatch.setattr(command, "MAX_SECONDS", 0.05)
    result = await registry(tmp_path, lambda argv, cwd: True).execute(command_call(["sleep", "10"]))
    assert "timed out" in result.content
    assert "started" in result.content
    assert fake_docker.calls[-1][0] == "rm"
    assert not fake_docker.snapshot.exists()


@pytest.mark.asyncio
async def test_command_limits_output_and_removes_container(tmp_path, fake_docker, monkeypatch):
    monkeypatch.setattr(command, "MAX_OUTPUT_BYTES", 64)
    fake_docker.output = b"x" * 1000
    result = await registry(tmp_path, lambda argv, cwd: True).execute(command_call(["yes"]))
    assert "output exceeded 64 bytes" in result.content
    assert len(result.content) < 200
    assert fake_docker.calls[-1][0] == "rm"


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["create", "start"])
async def test_command_cancellation_requests_container_removal(tmp_path, fake_docker, phase):
    setattr(fake_docker, "block_" + phase, True)
    task = asyncio.create_task(registry(tmp_path, lambda argv, cwd: True).execute(command_call(["sleep", "10"])))
    await asyncio.wait_for(fake_docker.creating.wait() if phase == "create" else fake_docker.started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert fake_docker.calls[-1][0] == "rm"
    assert fake_docker.calls[-1][-1] == fake_docker.calls[0][fake_docker.calls[0].index("--name") + 1]
    assert not fake_docker.snapshot.exists()


@pytest.mark.asyncio
async def test_command_reports_cleanup_failure(tmp_path, fake_docker):
    fake_docker.cleanup_error = b"daemon unavailable"
    result = await registry(tmp_path, lambda argv, cwd: True).execute(command_call(["python", "-V"]))
    assert result.is_error
    assert "cleanup failed" in result.content


@pytest.mark.asyncio
async def test_command_preserves_cancellation_when_cleanup_fails(tmp_path, fake_docker):
    fake_docker.block_start = True
    fake_docker.cleanup_error = b"daemon unavailable"
    task = asyncio.create_task(registry(tmp_path, lambda argv, cwd: True).execute(command_call(["sleep", "10"])))
    await asyncio.wait_for(fake_docker.started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.skipif(os.name == "nt", reason="Container bootstrap uses Linux exec semantics")
@pytest.mark.asyncio
async def test_container_bootstrap_writes_only_copy_and_preserves_literal_arguments(tmp_path):
    source = tmp_path / "input"
    workspace = tmp_path / "workspace"
    source.mkdir()
    workspace.mkdir()
    (source / "note.txt").write_text("original")
    literal = "value; touch unintended-file"
    argv = [sys.executable, "-c", (
        "import sys; from pathlib import Path; "
        "Path('note.txt').write_text(sys.argv[1]); print(sys.argv[1])"
    ), literal]
    bootstrap = command.CONTAINER_BOOTSTRAP.replace(
        "'/input'", repr(str(source)),
    ).replace("'/workspace'", repr(str(workspace)))
    code, output, exceeded = await command._docker(
        sys.executable, "-c", bootstrap, json.dumps(argv), str(workspace),
    )
    assert code == 0
    assert not exceeded
    assert literal.encode() in output
    assert (workspace / "note.txt").read_text() == literal
    assert (source / "note.txt").read_text() == "original"
    assert not (workspace / "unintended-file").exists()


@pytest.mark.asyncio
async def test_docker_client_bounds_real_subprocess_output(monkeypatch):
    monkeypatch.setattr(command, "MAX_OUTPUT_BYTES", 64)
    code, output, exceeded = await command._docker(sys.executable, "-c", "print('x' * 100000)")
    assert len(output) == 64
    assert exceeded


@pytest.mark.asyncio
async def test_docker_client_cancellation_reaps_local_process(monkeypatch):
    started = asyncio.Event()
    processes = []
    original_spawn = asyncio.create_subprocess_exec

    async def spawn(*args, **kwargs):
        process = await original_spawn(*args, **kwargs)
        processes.append(process)
        started.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(command._docker(sys.executable, "-c", "import time; time.sleep(20)"))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert processes[0].returncode is not None


@pytest.mark.skipif(os.environ.get("LARIO_TEST_DOCKER") != "1", reason="opt-in: requires local Linux Docker and a prebuilt sandbox image")
@pytest.mark.asyncio
async def test_real_docker_isolates_files_and_network(tmp_path):
    (tmp_path / "note.txt").write_text("original", encoding="utf-8")
    script = """
import os, socket, sys
from pathlib import Path
assert os.getuid() == 65534
assert Path('note.txt').read_text() == 'original'
Path('note.txt').write_text('container-only')
for path in ('/input/note.txt', '/etc/pyharness-forbidden'):
    try:
        Path(path).write_text('forbidden')
    except OSError:
        pass
    else:
        raise AssertionError('write escaped workspace copy')
try:
    socket.create_connection(('1.1.1.1', 53), timeout=0.5)
except OSError:
    pass
else:
    raise AssertionError('network is available')
print('isolated-ok')
sys.exit(7)
"""
    result = await registry(tmp_path, lambda argv, cwd: True).execute(
        command_call(["python", "-c", "exec(" + repr(script) + ")"])
    )
    assert not result.is_error
    assert "isolated-ok" in result.content
    assert "exit_code: 7" in result.content
    assert (tmp_path / "note.txt").read_text() == "original"
