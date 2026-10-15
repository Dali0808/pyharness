from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re
import shlex
import shutil
import stat
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from agent.tools import AgentTool
from coding_agent.builtins import resolve_workspace_path

MAX_SECONDS = 30
MAX_OUTPUT_BYTES = 32_000
MAX_SNAPSHOT_BYTES = 64_000_000
MAX_SNAPSHOT_ENTRIES = 10_000
CLEANUP_SECONDS = 10
DEFAULT_IMAGE = "pyharness-sandbox:latest"
SNAPSHOT_EXCLUDES = {
    ".git", ".runtime", ".eval", ".venv", "venv", "__pycache__",
    ".pytest_cache", ".uv-cache", "node_modules",
}
CommandApproval = Callable[[list[str], Path], bool | Awaitable[bool]]

# The input mount is read-only; command writes stay in bounded container tmpfs.
CONTAINER_BOOTSTRAP = (
    "import json, os, shutil, sys; "
    "shutil.copytree('/input', '/workspace', dirs_exist_ok=True); "
    "os.chdir(sys.argv[2]); "
    "argv = json.loads(sys.argv[1]); "
    "os.execvpe(argv[0], argv, os.environ)"
)


class RunCommandArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    argv: list[str] = Field(min_length=1, max_length=24)
    cwd: str = "."


async def _copy_workspace(root: Path, target: Path) -> None:
    total_bytes = 0
    entries = 0

    async def copy_directory(source: Path, destination: Path) -> None:
        nonlocal total_bytes, entries
        destination.mkdir(mode=0o755)
        for path in source.iterdir():
            if path.name in SNAPSHOT_EXCLUDES or path.name.startswith(".env"):
                continue
            entries += 1
            if entries > MAX_SNAPSHOT_ENTRIES:
                raise ValueError("workspace snapshot exceeds the entry limit")
            info = path.lstat()
            is_reparse_point = getattr(info, "st_file_attributes", 0) & getattr(
                stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0,
            )
            if path.is_symlink() or is_reparse_point:
                raise ValueError(f"workspace snapshot rejects symbolic links: {path.relative_to(root)}")
            resolve_workspace_path(root, str(path.relative_to(root)))
            output = destination / path.name
            if stat.S_ISDIR(info.st_mode):
                await copy_directory(path, output)
            elif stat.S_ISREG(info.st_mode):
                total_bytes += info.st_size
                if total_bytes > MAX_SNAPSHOT_BYTES:
                    raise ValueError("workspace snapshot exceeds the byte limit")
                flags = (
                    os.O_RDONLY | getattr(os, "O_BINARY", 0)
                    | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
                )
                with os.fdopen(os.open(path, flags), "rb") as reader, output.open("wb") as writer:
                    opened = os.fstat(reader.fileno())
                    if (
                        not stat.S_ISREG(opened.st_mode)
                        or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
                    ):
                        raise ValueError("workspace changed while creating the snapshot")
                    copied = 0
                    while chunk := reader.read(64_000):
                        copied += len(chunk)
                        if total_bytes - info.st_size + copied > MAX_SNAPSHOT_BYTES:
                            raise ValueError("workspace snapshot exceeds the byte limit")
                        writer.write(chunk)
                        await asyncio.sleep(0)
                total_bytes += copied - info.st_size
                output.chmod(0o644 | (info.st_mode & 0o111))
            else:
                raise ValueError("workspace snapshot accepts only regular files and directories")
            await asyncio.sleep(0)

    await copy_directory(root, target)


async def _docker(
    docker: str, *args: str, captured: bytearray | None = None,
) -> tuple[int, bytes, bool]:
    process = await asyncio.create_subprocess_exec(
        docker, *args, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    output = captured if captured is not None else bytearray()
    exceeded = False
    try:
        assert process.stdout is not None
        while chunk := await process.stdout.read(4096):
            remaining = MAX_OUTPUT_BYTES - len(output)
            output.extend(chunk[:remaining])
            if len(chunk) > remaining:
                exceeded = True
                break
        if not exceeded:
            await process.wait()
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        await process.communicate()
    assert process.returncode is not None
    return process.returncode, bytes(output), exceeded


async def _remove_container(docker: str, name: str) -> None:
    try:
        async with asyncio.timeout(CLEANUP_SECONDS):
            code, output, exceeded = await _docker(docker, "rm", "--force", name)
    except TimeoutError as exc:
        raise RuntimeError(f"Docker sandbox cleanup timed out for {name}") from exc
    text = output.decode("utf-8", errors="replace")
    if exceeded or (code != 0 and "No such container" not in text):
        raise RuntimeError(f"Docker sandbox cleanup failed for {name}: {text.strip()}")


def create_run_command_tool(
    workspace_root: Path,
    approve: CommandApproval,
) -> AgentTool:
    root = Path(workspace_root).resolve(strict=True)

    async def run_command(arguments: BaseModel) -> str:
        if not isinstance(arguments, RunCommandArgs):
            raise TypeError("run_command received unexpected arguments")
        argv = arguments.argv
        if any(not arg or len(arg) > 1024 or any(c in arg for c in "\x00\r\n") for arg in argv):
            raise ValueError("command arguments must be short, nonempty single lines")
        cwd = resolve_workspace_path(root, arguments.cwd)
        if not cwd.is_dir():
            raise NotADirectoryError(arguments.cwd)
        image = os.environ.get("LARIO_SANDBOX_IMAGE", DEFAULT_IMAGE)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:@-]{0,255}", image):
            raise ValueError("LARIO_SANDBOX_IMAGE must be a Docker image reference")

        decision = approve(argv, cwd)
        if inspect.isawaitable(decision):
            decision = await decision
        if not decision:
            raise PermissionError("command was rejected")
        # Revalidate the directory after an asynchronous approval.
        cwd = resolve_workspace_path(root, arguments.cwd)
        if not cwd.is_dir():
            raise NotADirectoryError(arguments.cwd)
        docker = shutil.which("docker")
        if docker is None:
            raise RuntimeError("Docker sandbox is unavailable: Docker CLI was not found")

        container_cwd = "/workspace"
        relative_cwd = cwd.relative_to(root).as_posix()
        if relative_cwd != ".":
            container_cwd += "/" + relative_cwd
        name = "pyharness-command-" + uuid4().hex
        attempted = False
        code = -1
        output = b""
        captured = bytearray()
        failure: str | None = None
        cancellation: asyncio.CancelledError | None = None
        with tempfile.TemporaryDirectory(prefix="pyharness-command-") as scratch:
            snapshot = Path(scratch) / "snapshot"
            try:
                async with asyncio.timeout(MAX_SECONDS):
                    await _copy_workspace(root, snapshot)
                    if "," in str(snapshot):
                        raise ValueError("Docker snapshot path cannot contain a comma")
                    attempted = True
                    create_code, create_output, exceeded = await _docker(
                        docker, "create", "--name", name, "--pull", "never",
                        "--network", "none", "--read-only", "--user", "65534:65534",
                        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                        "--memory", "512m", "--memory-swap", "512m", "--cpus", "1",
                        "--pids-limit", "64", "--workdir", "/workspace",
                        "--log-driver", "none",
                        "--mount", f"type=bind,source={snapshot},target=/input,readonly,bind-recursive=disabled",
                        "--tmpfs", "/workspace:rw,nosuid,nodev,size=256m,mode=0700,uid=65534,gid=65534",
                        "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m,mode=1777",
                        "--env", "HOME=/tmp", "--env", "TMPDIR=/tmp",
                        "--env", "UV_CACHE_DIR=/tmp/uv-cache",
                        "--env", "PYTHONDONTWRITEBYTECODE=1",
                        "--entrypoint", "python", image,
                        "-c", CONTAINER_BOOTSTRAP, json.dumps(argv), container_cwd,
                    )
                    if create_code != 0 or exceeded:
                        detail = create_output.decode("utf-8", errors="replace").strip()
                        raise RuntimeError(f"Docker sandbox is unavailable: {detail}")
                    code, output, exceeded = await _docker(
                        docker, "start", "--attach", name, captured=captured,
                    )
                    if exceeded:
                        failure = f"output exceeded {MAX_OUTPUT_BYTES} bytes"
                    else:
                        inspect_code, state_output, inspect_exceeded = await _docker(
                            docker, "inspect", "--format", "{{json .State}}", name,
                        )
                        if inspect_code != 0 or inspect_exceeded:
                            raise RuntimeError("Docker sandbox exit status is unavailable")
                        state = json.loads(state_output)
                        if (
                            not isinstance(state, dict)
                            or state.get("Running") is not False
                            or type(state.get("ExitCode")) is not int
                        ):
                            raise RuntimeError("Docker sandbox returned an invalid exit status")
                        if state.get("Error"):
                            raise RuntimeError(f"Docker sandbox failed to start: {state['Error']}")
                        if not state.get("StartedAt") or state["StartedAt"].startswith("0001-"):
                            raise RuntimeError("Docker sandbox command was not started")
                        if code != 0 and state["ExitCode"] == 0:
                            raise RuntimeError("Docker sandbox command attachment failed")
                        code = state["ExitCode"]
            except TimeoutError:
                output = bytes(captured)
                failure = f"timed out after {MAX_SECONDS}s"
            except asyncio.CancelledError as exc:
                cancellation = exc
                raise
            finally:
                if attempted:
                    cleanup = asyncio.create_task(_remove_container(docker, name))
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        await cleanup
                        raise
                    except Exception as exc:
                        if cancellation is None:
                            raise
                        cancellation.add_note(str(exc))
                        logging.getLogger(__name__).warning(
                            "Docker container %s could not be removed after cancellation", name,
                        )

        text = output.decode("utf-8", errors="replace").strip()
        summary = f"\nerror: {failure}" if failure else ""
        return f"exit_code: {code}{summary}\noutput:\n{text or '(none)'}"

    return AgentTool.from_handler(
        name="run_command",
        description=(
            "Run one existing project test, build, or short startup check after approval "
            "inside an offline Linux Docker sandbox using a disposable workspace copy. "
            "Command changes are discarded. Use executables installed in the sandbox "
            "image, not host paths or the host .venv. Pass executable and arguments "
            "separately, without shell syntax. Do not run pytest unless the user explicitly requests it."
        ),
        args_model=RunCommandArgs,
        handler=run_command,
    )


def format_command(argv: list[str], cwd: Path) -> str:
    image = os.environ.get("LARIO_SANDBOX_IMAGE", DEFAULT_IMAGE)
    return (
        f"execution: Docker sandbox (disposable workspace copy)\nimage: {image}\n"
        f"working directory: {cwd}\ncommand: {shlex.join(argv)}"
    )
