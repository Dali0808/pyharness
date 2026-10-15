from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from coding_agent import command


class DockerStub:
    """Offline Docker lifecycle; never starts a daemon or executes model code."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.snapshot: Path | None = None
        self.files: dict[str, bytes] = {}
        self.output = b"checked\n"
        self.exit_code = 0
        self.create_error: bytes | None = None
        self.cleanup_error: bytes | None = None
        self.state: bytes | None = None
        self.block_create = False
        self.block_start = False
        self.started = asyncio.Event()
        self.creating = asyncio.Event()

    async def execute(self, docker: str, *args: str, captured=None):
        self.calls.append(args)
        if args[0] == "create":
            mount = args[args.index("--mount") + 1]
            self.snapshot = Path(mount.split("source=", 1)[1].split(",target=", 1)[0])
            self.files = {
                path.relative_to(self.snapshot).as_posix(): path.read_bytes()
                for path in self.snapshot.rglob("*") if path.is_file()
            }
            self.creating.set()
            if self.block_create:
                await asyncio.Event().wait()
            if self.create_error is not None:
                return 1, self.create_error, False
            return 0, b"container-id\n", False
        if args[0] == "start":
            self.started.set()
            output = self.output[:command.MAX_OUTPUT_BYTES]
            if captured is not None:
                captured.extend(output)
            if self.block_start:
                await asyncio.Event().wait()
            # docker start's CLI status need not be the container exit status.
            return 0, output, len(self.output) > command.MAX_OUTPUT_BYTES
        if args[0] == "inspect":
            return 0, self.state or json.dumps({
                "Running": False, "ExitCode": self.exit_code, "Error": "",
                "StartedAt": "2026-10-13T00:00:00Z",
            }).encode(), False
        if args[0] == "rm":
            if self.cleanup_error is not None:
                return 1, self.cleanup_error, False
            return 0, b"removed\n", False
        raise AssertionError(f"Unexpected Docker operation: {args}")


@pytest.fixture
def fake_docker(monkeypatch) -> DockerStub:
    fake = DockerStub()
    monkeypatch.delenv("LARIO_SANDBOX_IMAGE", raising=False)
    original_which = command.shutil.which
    monkeypatch.setattr(
        command.shutil, "which",
        lambda name: "docker-stub" if name == "docker" else original_which(name),
    )
    monkeypatch.setattr(command, "_docker", fake.execute)
    return fake
