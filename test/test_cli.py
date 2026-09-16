from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest

from ai.provider import ScriptedProvider
from ai.schemas import (
    AssistantMessage,
    ChatResponse,
    TextPart,
    ToolCallPart,
)
from coding_agent.cli import CliConfig, main, parse_args


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