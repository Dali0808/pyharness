from __future__ import annotations

from pathlib import Path
from datetime import datetime
from typing import Annotated, Literal, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
)

from ai.schemas import Message, ModelSpec


FORMAT_VERSION = 1


class _SessionSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionMetadata(_SessionSchema):
    session_id: str
    created_at: datetime
    workspace: str
    system_prompt: str
    model: ModelSpec


class SessionSnapshot(_SessionSchema):
    metadata: SessionMetadata
    messages: list[Message] = Field(default_factory=list)


class SessionFormatError(ValueError):
    """Raised when a session file violates the JSONL format."""


class _SessionHeader(_SessionSchema):
    type: Literal["session"] = "session"
    version: Literal[1] = FORMAT_VERSION
    metadata: SessionMetadata


class _MessageRecord(_SessionSchema):
    type: Literal["message"] = "message"
    message: Message


_SessionRecord: TypeAlias = Annotated[
    _SessionHeader | _MessageRecord,
    Field(discriminator="type"),
]

_RECORD_ADAPTER = TypeAdapter(_SessionRecord)


class JsonlSessionStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def create(self, metadata: SessionMetadata) -> None:
        header = _SessionHeader(metadata=metadata)

        # Exclusive creation prevents accidental session loss.
        with self.path.open(
            "x",
            encoding="utf-8",
            newline="\n",
        ) as stream:
            stream.write(header.model_dump_json())
            stream.write("\n")

    def append_message(self, message: Message) -> None:
        if not self.path.is_file():
            raise FileNotFoundError(
                f"session file does not exist: {self.path}"
            )

        record = _MessageRecord(message=message)

        with self.path.open(
            "a",
            encoding="utf-8",
            newline="\n",
        ) as stream:
            stream.write(record.model_dump_json())
            stream.write("\n")

    def load(self) -> SessionSnapshot:
        header: _SessionHeader | None = None
        messages: list[Message] = []

        with self.path.open(
            "r",
            encoding="utf-8",
        ) as stream:
            for line_number, line in enumerate(stream, start=1):
                try:
                    record = _RECORD_ADAPTER.validate_json(line)
                except ValidationError as exc:
                    raise SessionFormatError(
                        "invalid session record "
                        f"at line {line_number}"
                    ) from exc

                if line_number == 1:
                    if not isinstance(record, _SessionHeader):
                        raise SessionFormatError(
                            "the first record must be "
                            "a session header"
                        )
                    header = record
                    continue

                if isinstance(record, _SessionHeader):
                    raise SessionFormatError(
                        "session header may only appear "
                        "on the first line"
                    )

                messages.append(record.message)

        if header is None:
            raise SessionFormatError("session file is empty")

        return SessionSnapshot(
            metadata=header.metadata,
            messages=messages,
        )