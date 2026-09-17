from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ai.schemas import (
    AssistantMessage,
    ModelSpec,
    ToolCallPart,
    ToolResultMessage,
    UserMessage,
)

from coding_agent.session import (
    JsonlSessionStore,
    SessionFormatError,
    SessionMetadata,
)


CREATED_AT = datetime(2026, 9, 17, 8, 0, tzinfo=timezone.utc)


def make_metadata(workspace: Path) -> SessionMetadata:
    return SessionMetadata(
        session_id="session-001",
        created_at=CREATED_AT,
        workspace=str(workspace.resolve()),
        system_prompt="You are a coding assistant.",
        model=ModelSpec(
            provider="scripted",
            id="test-model",
        ),
    )


def test_create_writes_versioned_session_header(
    tmp_path: Path,
) -> None:
    session_path = tmp_path / "session.jsonl"
    metadata = make_metadata(tmp_path)

    JsonlSessionStore(session_path).create(metadata)

    lines = session_path.read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(lines) == 1

    record = json.loads(lines[0])

    assert record["type"] == "session"
    assert record["version"] == 1
    assert record["metadata"]["session_id"] == "session-001"
    assert record["metadata"]["workspace"] == str(
        tmp_path.resolve()
    )
    assert (
        record["metadata"]["system_prompt"]
        == "You are a coding assistant."
    )
    assert record["metadata"]["model"]["provider"] == "scripted"
    assert record["metadata"]["model"]["id"] == "test-model"


def test_messages_round_trip_in_append_order(
    tmp_path: Path,
) -> None:
    store = JsonlSessionStore(tmp_path / "session.jsonl")
    metadata = make_metadata(tmp_path)

    messages = [
        UserMessage(
            content="读取任务文件。",
            timestamp=datetime(
                2026,
                9,
                17,
                8,
                1,
                tzinfo=timezone.utc,
            ),
        ),
        AssistantMessage(
            content=[
                ToolCallPart(
                    id="call-1",
                    name="read_file",
                    arguments_json='{"path":"task.txt"}',
                )
            ],
            provider="scripted",
            model="test-model",
            response_id="response-1",
            timestamp=datetime(
                2026,
                9,
                17,
                8,
                2,
                tzinfo=timezone.utc,
            ),
        ),
        ToolResultMessage(
            tool_call_id="call-1",
            tool_name="read_file",
            content="task contents",
            timestamp=datetime(
                2026,
                9,
                17,
                8,
                3,
                tzinfo=timezone.utc,
            ),
        ),
    ]

    store.create(metadata)

    for message in messages:
        store.append_message(message)

    snapshot = store.load()

    assert snapshot.metadata == metadata
    assert snapshot.messages == messages
    assert [
        message.role
        for message in snapshot.messages
    ] == [
        "user",
        "assistant",
        "tool_result",
    ]


def test_create_refuses_to_overwrite_existing_session(
    tmp_path: Path,
) -> None:
    session_path = tmp_path / "session.jsonl"
    store = JsonlSessionStore(session_path)
    metadata = make_metadata(tmp_path)

    store.create(metadata)
    original_content = session_path.read_text(encoding="utf-8")

    with pytest.raises(FileExistsError):
        store.create(metadata)

    assert session_path.read_text(
        encoding="utf-8"
    ) == original_content


def test_append_message_requires_existing_session(
    tmp_path: Path,
) -> None:
    session_path = tmp_path / "missing.jsonl"
    store = JsonlSessionStore(session_path)
    message = UserMessage(
        content="This must not create a session.",
        timestamp=CREATED_AT,
    )

    with pytest.raises(
        FileNotFoundError,
        match="session file does not exist",
    ):
        store.append_message(message)

    assert not session_path.exists()


def test_load_rejects_empty_session_file(
    tmp_path: Path,
) -> None:
    session_path = tmp_path / "session.jsonl"
    session_path.write_text("", encoding="utf-8")

    with pytest.raises(
        SessionFormatError,
        match="session file is empty",
    ):
        JsonlSessionStore(session_path).load()


def test_load_reports_invalid_json_line_number(
    tmp_path: Path,
) -> None:
    session_path = tmp_path / "session.jsonl"
    store = JsonlSessionStore(session_path)
    store.create(make_metadata(tmp_path))

    with session_path.open(
        "a",
        encoding="utf-8",
        newline="\n",
    ) as stream:
        stream.write('{"type":"message"\n')

    with pytest.raises(
        SessionFormatError,
        match="invalid session record at line 2",
    ):
        store.load()


def test_load_requires_session_header_as_first_record(
    tmp_path: Path,
) -> None:
    session_path = tmp_path / "session.jsonl"
    message = UserMessage(
        content="This record has no session header.",
        timestamp=CREATED_AT,
    )
    record = {
        "type": "message",
        "message": message.model_dump(mode="json"),
    }
    session_path.write_text(
        json.dumps(record, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        SessionFormatError,
        match="the first record must be a session header",
    ):
        JsonlSessionStore(session_path).load()


def test_load_rejects_duplicate_session_header(
    tmp_path: Path,
) -> None:
    session_path = tmp_path / "session.jsonl"
    store = JsonlSessionStore(session_path)
    store.create(make_metadata(tmp_path))

    header_line = session_path.read_text(encoding="utf-8")
    session_path.write_text(
        header_line + header_line,
        encoding="utf-8",
    )

    with pytest.raises(
        SessionFormatError,
        match="session header may only appear on the first line",
    ):
        store.load()


def test_load_rejects_unknown_format_version(
    tmp_path: Path,
) -> None:
    session_path = tmp_path / "session.jsonl"
    store = JsonlSessionStore(session_path)
    store.create(make_metadata(tmp_path))

    header = json.loads(
        session_path.read_text(encoding="utf-8")
    )
    header["version"] = 2
    session_path.write_text(
        json.dumps(header, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        SessionFormatError,
        match="invalid session record at line 1",
    ):
        store.load()