from __future__ import annotations

import asyncio
import inspect
import json
import os
import platform
import shlex
import shutil
import signal
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agent.tools import AgentTool
from coding_agent.builtins import resolve_workspace_path

MAX_SECONDS = 30
MAX_OUTPUT_BYTES = 32_000
CommandApproval = Callable[[list[str], Path], bool | Awaitable[bool]]


class RunCommandArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    argv: list[str] = Field(min_length=1, max_length=24)
    cwd: str = "."


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
        if platform.system() != "Darwin" or not Path("/usr/bin/sandbox-exec").is_file():
            raise RuntimeError("macOS process sandbox is unavailable")

        decision = approve(argv, cwd)
        if inspect.isawaitable(decision):
            decision = await decision
        if not decision:
            raise PermissionError("command was rejected")

        if "/" in argv[0]:
            candidate = Path(argv[0])
            launch_path = candidate if candidate.is_absolute() else cwd / candidate
        else:
            found = shutil.which(argv[0])
            if found is None:
                raise FileNotFoundError(argv[0])
            launch_path = Path(found).absolute()
        executable = launch_path.resolve(strict=True)
        if not executable.is_file():
            raise FileNotFoundError(argv[0])

        runtime_root = executable.parent.parent if executable.parent.name == "bin" else executable.parent
        if runtime_root in (Path.home(), Path("/")):
            runtime_root = executable.parent
        launch_root = launch_path.parent.parent if launch_path.parent.name == "bin" else launch_path.parent
        if launch_root in (Path.home(), Path("/")):
            launch_root = launch_path.parent
        read_paths = [root, runtime_root, launch_root, *(Path(path) for path in ("/usr", "/System", "/Library"))]
        profile = (
            "(version 1) (deny default) (allow process*) (allow mach-lookup) "
            "(allow file-read* file-write* (literal \"/dev/null\")) "
            "(allow file-read-metadata "
            + " ".join(f"(literal {json.dumps(str(path))})" for path in root.parents)
            + ") "
            "(allow file-read* (literal \"/\") "
            + " ".join(f"(subpath {json.dumps(str(path))})" for path in read_paths)
            + f") (allow file-write* (subpath {json.dumps(str(root))}))"
        )

        with tempfile.TemporaryDirectory(prefix=".pyharness-command-", dir=root) as scratch:
            env = {
                "PATH": os.environ.get("PATH", os.defpath),
                "HOME": scratch,
                "TMPDIR": scratch,
                "UV_CACHE_DIR": scratch,
                "PYTHONDONTWRITEBYTECODE": "1",
                "LANG": os.environ.get("LANG", "C"),
            }
            process = await asyncio.create_subprocess_exec(
                "/usr/bin/sandbox-exec", "-p", profile, str(launch_path), *argv[1:],
                cwd=cwd, env=env, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT, start_new_session=True,
            )
            output = bytearray()
            failure: str | None = None
            try:
                async with asyncio.timeout(MAX_SECONDS):
                    assert process.stdout is not None
                    while chunk := await process.stdout.read(4096):
                        output.extend(chunk)
                        if len(output) > MAX_OUTPUT_BYTES:
                            failure = f"output exceeded {MAX_OUTPUT_BYTES} bytes"
                            break
                    if failure is None:
                        await process.wait()
            except TimeoutError:
                failure = f"timed out after {MAX_SECONDS}s"
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                if process.stdout is not None:
                    await process.stdout.read()
                await process.wait()

        text = output[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace").strip()
        summary = f"\nerror: {failure}" if failure else ""
        return f"exit_code: {process.returncode}{summary}\noutput:\n{text or '(none)'}"

    return AgentTool.from_handler(
        name="run_command",
        description=(
            "Run one existing project test, build, or short startup check after approval "
            "inside the macOS sandbox. Pass executable and arguments separately, without "
            "shell syntax. Do not run pytest unless the user explicitly requests it."
        ),
        args_model=RunCommandArgs,
        handler=run_command,
    )


def format_command(argv: list[str], cwd: Path) -> str:
    return f"working directory: {cwd}\ncommand: {shlex.join(argv)}"
