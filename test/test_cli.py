from __future__ import annotations

from io import StringIO
from pathlib import Path
from datetime import datetime, timezone

import pytest

from ai.provider import ScriptedProvider
from ai.schemas import (
    AssistantMessage,
    ChatResponse,
    TextPart,
    ToolCallPart,
    ModelSpec,
    UserMessage,
    ToolResultMessage,
)
from coding_agent.cli import CliConfig, main, parse_args
from coding_agent.session import (
    JsonlSessionStore,
    SessionMetadata,
)

def test_main_runs_workspace_tool_task_and_renders_events(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provider = ScriptedProvider(
        [
            ChatResponse(
                message=AssistantMessage(
                    content=[
                        ToolCallPart(
                            id="call-1",
                            name="write_file",
                            arguments_json=(
                                '{"path":"greeting.txt",'
                                '"content":"hello"}'
                            ),
                        )
                    ]
                ),
                finish_reason="tool_calls",
            ),
            ChatResponse(
                message=AssistantMessage(
                    content=[
                        TextPart(
                            text="Created greeting.txt."
                        )
                    ]
                ),
                finish_reason="stop",
            ),
        ]
    )
    seen: dict[str, CliConfig] = {}
    output = StringIO()

    def provider_factory(config: CliConfig) -> ScriptedProvider:
        seen["config"] = config
        return provider

    exit_code = main(
        [
            "Create greeting.txt.",
            "--workspace",
            str(workspace),
            "--provider-id",
            "scripted",
            "--model",
            "test-model",
        ],
        provider_factory=provider_factory,
        output=output,
    )

    assert exit_code == 0
    assert seen["config"].workspace == workspace.resolve()
    assert seen["config"].provider_id == "scripted"
    assert seen["config"].model_id == "test-model"
    assert (workspace / "greeting.txt").read_text(
        encoding="utf-8"
    ) == "hello"

    assert len(provider.requests) == 2
    assert [
        tool.name
        for tool in provider.requests[0].tools
    ] == [
        "read_file",
        "write_file",
        "list_dir",
    ]
    assert [
        message.role
        for message in provider.requests[1].messages
    ] == [
        "user",
        "assistant",
        "tool_result",
    ]

    rendered = output.getvalue()
    assert "run started" in rendered
    assert "step 1: tool started: write_file" in rendered
    assert "step 1: tool finished: write_file" in rendered
    assert "run finished after 2 step(s)" in rendered
    assert "answer: Created greeting.txt." in rendered


def test_main_returns_nonzero_for_agent_failure(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provider = ScriptedProvider(
        [
            ChatResponse(
                message=AssistantMessage(
                    content=[TextPart(text="Partial response")]
                ),
                finish_reason="length",
            )
        ]
    )
    output = StringIO()

    exit_code = main(
        [
            "Write a long response.",
            "--workspace",
            str(workspace),
            "--provider-id",
            "scripted",
            "--model",
            "test-model",
        ],
        provider_factory=lambda _: provider,
        output=output,
    )

    assert exit_code == 1
    assert len(provider.requests) == 1

    rendered = output.getvalue()
    assert (
        "run failed after 1 step(s): response_truncated"
        in rendered
    )
    assert (
        "failed (response_truncated): "
        "Model response was truncated before completion."
        in rendered
    )


def test_parse_args_rejects_non_positive_max_steps(
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        parse_args(
            [
                "Do work.",
                "--workspace",
                str(tmp_path),
                "--model",
                "test-model",
                "--max-steps",
                "0",
            ]
        )

    assert exc_info.value.code == 2


def test_parse_args_defaults_session_path_to_none(
    tmp_path: Path,
) -> None:
    config = parse_args(
        [
            "Do work.",
            "--workspace",
            str(tmp_path),
            "--model",
            "test-model",
        ]
    )

    assert config.session_path is None


def test_parse_args_resolves_session_path_inside_workspace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    config = parse_args(
        [
            "Do work.",
            "--workspace",
            str(workspace),
            "--model",
            "test-model",
            "--session",
            "history.jsonl",
        ]
    )

    assert config.session_path == (
        workspace.resolve() / "history.jsonl"
    )


def test_parse_args_rejects_session_path_outside_workspace(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(SystemExit) as exc_info:
        parse_args(
            [
                "Do work.",
                "--workspace",
                str(workspace),
                "--model",
                "test-model",
                "--session",
                "../history.jsonl",
            ]
        )

    assert exc_info.value.code == 2
    assert "invalid session path" in capsys.readouterr().err


def test_main_creates_new_session_header(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session_path = workspace / "history.jsonl"
    provider = ScriptedProvider(
        [
            ChatResponse(
                message=AssistantMessage(
                    content=[TextPart(text="Done.")]
                ),
                finish_reason="stop",
            )
        ]
    )

    exit_code = main(
        [
            "Start new work.",
            "--workspace",
            str(workspace),
            "--provider-id",
            "scripted",
            "--model",
            "test-model",
            "--session",
            "history.jsonl",
        ],
        provider_factory=lambda _: provider,
        output=StringIO(),
    )

    assert exit_code == 0
    assert session_path.is_file()

    snapshot = JsonlSessionStore(session_path).load()

    assert snapshot.metadata.session_id
    assert snapshot.metadata.workspace == str(workspace.resolve())
    assert snapshot.metadata.model == ModelSpec(
        provider="scripted",
        id="test-model",
    )
    assert snapshot.metadata.created_at.tzinfo is not None


def test_main_loads_session_history_into_first_request(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session_path = workspace / "history.jsonl"
    system_prompt = "Continue the existing coding session."
    store = JsonlSessionStore(session_path)
    previous_user = UserMessage(
        content="Inspect the existing project.",
        timestamp=datetime(
            2026,
            9,
            17,
            8,
            0,
            tzinfo=timezone.utc,
        ),
    )
    previous_assistant = AssistantMessage(
        content=[TextPart(text="The project uses Python.")],
        provider="scripted",
        model="test-model",
        timestamp=datetime(
            2026,
            9,
            17,
            8,
            1,
            tzinfo=timezone.utc,
        ),
    )

    store.create(
        SessionMetadata(
            session_id="session-existing",
            created_at=datetime(
                2026,
                9,
                17,
                7,
                59,
                tzinfo=timezone.utc,
            ),
            workspace=str(workspace.resolve()),
            system_prompt=system_prompt,
            model=ModelSpec(
                provider="scripted",
                id="test-model",
            ),
        )
    )
    store.append_message(previous_user)
    store.append_message(previous_assistant)

    provider = ScriptedProvider(
        [
            ChatResponse(
                message=AssistantMessage(
                    content=[TextPart(text="Continuing.")],
                ),
                finish_reason="stop",
            )
        ]
    )

    exit_code = main(
        [
            "What should we do next?",
            "--workspace",
            str(workspace),
            "--provider-id",
            "scripted",
            "--model",
            "test-model",
            "--system-prompt",
            system_prompt,
            "--session",
            "history.jsonl",
        ],
        provider_factory=lambda _: provider,
        output=StringIO(),
    )

    assert exit_code == 0
    assert len(provider.requests) == 1

    request = provider.requests[0]

    assert request.system_prompt == system_prompt
    assert request.messages[:2] == [
        previous_user,
        previous_assistant,
    ]
    assert [
        message.role
        for message in request.messages
    ] == [
        "user",
        "assistant",
        "user",
    ]

    current_user = request.messages[-1]
    assert isinstance(current_user, UserMessage)
    assert current_user.content == "What should we do next?"


def test_main_persists_run_messages_in_session_order(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session_path = workspace / "history.jsonl"
    provider = ScriptedProvider(
        [
            ChatResponse(
                message=AssistantMessage(
                    content=[
                        ToolCallPart(
                            id="call-1",
                            name="write_file",
                            arguments_json=(
                                '{"path":"result.txt",'
                                '"content":"hello"}'
                            ),
                        )
                    ]
                ),
                finish_reason="tool_calls",
            ),
            ChatResponse(
                message=AssistantMessage(
                    content=[TextPart(text="Created result.txt.")],
                ),
                finish_reason="stop",
            ),
        ]
    )

    exit_code = main(
        [
            "Create result.txt.",
            "--workspace",
            str(workspace),
            "--provider-id",
            "scripted",
            "--model",
            "test-model",
            "--session",
            "history.jsonl",
        ],
        provider_factory=lambda _: provider,
        output=StringIO(),
    )

    assert exit_code == 0

    snapshot = JsonlSessionStore(session_path).load()

    assert [
        message.role
        for message in snapshot.messages
    ] == [
        "user",
        "assistant",
        "tool_result",
        "assistant",
    ]

    user_message = snapshot.messages[0]
    assert isinstance(user_message, UserMessage)
    assert user_message.content == "Create result.txt."

    tool_call_message = snapshot.messages[1]
    assert isinstance(tool_call_message, AssistantMessage)

    tool_call = tool_call_message.content[0]
    assert isinstance(tool_call, ToolCallPart)
    assert tool_call.id == "call-1"
    assert tool_call.name == "write_file"

    tool_result = snapshot.messages[2]
    assert isinstance(tool_result, ToolResultMessage)
    assert tool_result.tool_call_id == "call-1"
    assert tool_result.tool_name == "write_file"
    assert tool_result.content == "wrote file: result.txt"
    assert tool_result.is_error is False

    final_message = snapshot.messages[3]
    assert isinstance(final_message, AssistantMessage)

    final_text = final_message.content[0]
    assert isinstance(final_text, TextPart)
    assert final_text.text == "Created result.txt."