from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ai.schemas import (
    AssistantMessage,
    ModelSpec,
    ToolCallPart,
    ToolResultMessage,
    UserMessage,
)
from coding_agent.session import JsonlSessionStore, SessionMetadata


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