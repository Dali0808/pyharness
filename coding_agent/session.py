from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from ai.schemas import Message, ModelSpec, ToolCallPart, ToolResultMessage


FORMAT_VERSION = 2
RunStatus: TypeAlias = Literal["running", "completed", "failed", "interrupted"]


class _SessionSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionMetadata(_SessionSchema):
    session_id: str
    created_at: datetime
    workspace: str
    system_prompt: str
    model: ModelSpec
    multi_agent: bool = False


class SessionRun(_SessionSchema):
    run_id: str
    turn_id: str
    status: RunStatus
    failure_code: str | None = None


class SessionSnapshot(_SessionSchema):
    metadata: SessionMetadata
    messages: list[Message] = Field(default_factory=list)
    format_version: int = FORMAT_VERSION
    runs: list[SessionRun] = Field(default_factory=list)
    pending_tool_calls: list[ToolCallPart] = Field(default_factory=list)

    @property
    def active_run(self) -> SessionRun | None:
        if self.runs and self.runs[-1].status == "running":
            return self.runs[-1]
        return None


class SessionFormatError(ValueError):
    """Raised when a session file violates the JSONL format."""


class SessionCompatibilityError(ValueError):
    """Raised when a saved session cannot use the requested configuration."""


class _SessionHeader(_SessionSchema):
    type: Literal["session"] = "session"
    version: int = FORMAT_VERSION
    metadata: SessionMetadata


class _MessageRecord(_SessionSchema):
    type: Literal["message"] = "message"
    message: Message
    run_id: str | None = None


class _RunStartedRecord(_SessionSchema):
    type: Literal["run_started"] = "run_started"
    run_id: str
    turn_id: str


class _RunEndedRecord(_SessionSchema):
    type: Literal["run_ended"] = "run_ended"
    run_id: str
    status: Literal["completed", "failed", "interrupted"]
    failure_code: str | None = None


_SessionRecord: TypeAlias = Annotated[
    _SessionHeader | _MessageRecord | _RunStartedRecord | _RunEndedRecord,
    Field(discriminator="type"),
]
_RECORD_ADAPTER = TypeAdapter(_SessionRecord)


def validate_session_metadata(
    saved: SessionMetadata,
    *,
    workspace: Path,
    model: ModelSpec,
    system_prompt: str,
    multi_agent: bool = False,
) -> None:
    mismatches: list[str] = []
    if Path(saved.workspace).resolve(strict=False) != workspace.resolve(strict=False):
        mismatches.append("workspace")
    if saved.model != model:
        mismatches.append("model")
    if saved.system_prompt != system_prompt:
        mismatches.append("system prompt")
    if saved.multi_agent != multi_agent:
        mismatches.append("multi-agent mode")
    if mismatches:
        raise SessionCompatibilityError(
            "session configuration differs in "
            + ", ".join(mismatches)
            + "; use the saved configuration or start a new session"
        )


class JsonlSessionStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def create(self, metadata: SessionMetadata) -> None:
        header = _SessionHeader(metadata=metadata)
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(header.model_dump_json() + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def append_message(
        self,
        message: Message,
        *,
        run_id: str | None = None,
    ) -> None:
        self._append(_MessageRecord(
            message=message,
            run_id=run_id,
        ))

    def start_run(self, run_id: str, turn_id: str) -> None:
        snapshot = self.load()
        if snapshot.format_version != FORMAT_VERSION:
            raise SessionFormatError("legacy session must be migrated before a run")
        if snapshot.active_run is not None:
            raise SessionFormatError("session already has an active run")
        self._append(_RunStartedRecord(run_id=run_id, turn_id=turn_id))

    def end_run(
        self,
        run_id: str,
        status: Literal["completed", "failed", "interrupted"],
        *,
        failure_code: str | None = None,
    ) -> None:
        snapshot = self.load()
        if snapshot.active_run is None or snapshot.active_run.run_id != run_id:
            raise SessionFormatError("run_ended has no matching active run")
        self._append(_RunEndedRecord(
            run_id=run_id,
            status=status,
            failure_code=failure_code,
        ))

    def load(self) -> SessionSnapshot:
        return self._load(repair_tail=False)

    def load_metadata(self) -> SessionMetadata:
        with self.path.open("rb") as stream:
            first_line = stream.readline()
        try:
            header = _RECORD_ADAPTER.validate_json(first_line)
        except ValidationError as exc:
            raise SessionFormatError("invalid session record at line 1") from exc
        if not isinstance(header, _SessionHeader) or header.version not in (1, FORMAT_VERSION):
            raise SessionFormatError("the first record must be a supported session header")
        return header.metadata

    def recover(self) -> SessionSnapshot:
        """Repair a torn tail and close unknown tool outcomes without replaying them."""
        snapshot = self._load(repair_tail=True)
        if snapshot.format_version == 1:
            self._migrate_v1(snapshot)
            snapshot = self.load()

        active = snapshot.active_run
        for call in snapshot.pending_tool_calls:
            self.append_message(
                ToolResultMessage(
                    tool_call_id=call.id,
                    tool_name=call.name,
                    content=(
                        "interrupted_tool_call: no durable result was recorded; "
                        "execution outcome is unknown. Inspect the workspace "
                        "before deciding whether to retry."
                    ),
                    is_error=True,
                ),
                run_id=active.run_id if active else None,
            )
        if active is not None:
            self.end_run(active.run_id, "interrupted", failure_code="interrupted")
        return self.load()

    def _append(self, record: _SessionRecord) -> None:
        if not self.path.is_file():
            raise FileNotFoundError(f"session file does not exist: {self.path}")
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(record.model_dump_json() + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def _load(self, *, repair_tail: bool) -> SessionSnapshot:
        header: _SessionHeader | None = None
        messages: list[Message] = []
        runs: list[SessionRun] = []
        pending: dict[str, ToolCallPart] = {}
        active: SessionRun | None = None
        file_size = self.path.stat().st_size
        valid_bytes = 0
        last_line_terminated = True

        for line_number, line, is_last, end_offset in self._record_lines():
            last_line_terminated = line.endswith(b"\n")
            try:
                record = _RECORD_ADAPTER.validate_json(line)
            except ValidationError as exc:
                if repair_tail and is_last and not last_line_terminated:
                    with self.path.open("r+b") as stream:
                        stream.truncate(valid_bytes)
                        os.fsync(stream.fileno())
                    break
                raise SessionFormatError(
                    f"invalid session record at line {line_number}"
                ) from exc

            valid_bytes = end_offset
            if line_number == 1:
                if not isinstance(record, _SessionHeader):
                    raise SessionFormatError("the first record must be a session header")
                if record.version not in (1, FORMAT_VERSION):
                    raise SessionFormatError("invalid session record at line 1")
                header = record
                continue

            if isinstance(record, _SessionHeader):
                raise SessionFormatError("session header may only appear on the first line")
            if header is None:
                raise SessionFormatError("the first record must be a session header")
            if header.version == 1 and not isinstance(record, _MessageRecord):
                raise SessionFormatError("legacy session contains a v2 record")

            if isinstance(record, _RunStartedRecord):
                if active is not None:
                    raise SessionFormatError("session contains overlapping runs")
                if any(run.run_id == record.run_id for run in runs):
                    raise SessionFormatError("session contains a duplicate run ID")
                active = SessionRun(
                    run_id=record.run_id,
                    turn_id=record.turn_id,
                    status="running",
                )
                runs.append(active)
            elif isinstance(record, _RunEndedRecord):
                if active is None or active.run_id != record.run_id:
                    raise SessionFormatError("run_ended has no matching active run")
                runs[-1] = SessionRun(
                    run_id=active.run_id,
                    turn_id=active.turn_id,
                    status=record.status,
                    failure_code=record.failure_code,
                )
                active = None
            else:
                if record.run_id is not None:
                    if active is None or record.run_id != active.run_id:
                        raise SessionFormatError("message has no matching active run")
                if pending and record.message.role in ("user", "assistant"):
                    raise SessionFormatError(
                        "new message appeared before pending tool results"
                    )
                messages.append(record.message)
                if record.message.role == "assistant":
                    for part in record.message.content:
                        if isinstance(part, ToolCallPart):
                            if part.id in pending:
                                raise SessionFormatError("duplicate pending tool call ID")
                            pending[part.id] = part
                elif isinstance(record.message, ToolResultMessage):
                    if record.message.tool_call_id not in pending:
                        raise SessionFormatError("tool result has no matching call")
                    if (
                        record.message.tool_name
                        != pending[record.message.tool_call_id].name
                    ):
                        raise SessionFormatError("tool result name does not match call")
                    pending.pop(record.message.tool_call_id)

        if header is None:
            raise SessionFormatError("session file is empty")
        if (
            repair_tail
            and file_size
            and not last_line_terminated
            and valid_bytes == file_size
        ):
            with self.path.open("ab") as stream:
                stream.write(b"\n")
                stream.flush()
                os.fsync(stream.fileno())
        return SessionSnapshot(
            metadata=header.metadata,
            messages=messages,
            format_version=header.version,
            runs=runs,
            pending_tool_calls=list(pending.values()),
        )

    def _record_lines(self) -> Iterator[tuple[int, bytes, bool, int]]:
        with self.path.open("rb") as stream:
            file_size = os.fstat(stream.fileno()).st_size
            line_number = 0
            while line := stream.readline():
                line_number += 1
                end_offset = stream.tell()
                yield line_number, line, end_offset == file_size, end_offset

    def _migrate_v1(self, snapshot: SessionSnapshot) -> None:
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            dir=self.path.parent,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(_SessionHeader(metadata=snapshot.metadata).model_dump_json() + "\n")
                for message in snapshot.messages:
                    stream.write(_MessageRecord(message=message).model_dump_json() + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.path)
        finally:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)
