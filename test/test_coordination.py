from __future__ import annotations

import json
from dataclasses import replace
from io import StringIO
from pathlib import Path

import pytest

from ai.provider import ScriptedProvider
from ai.schemas import AssistantMessage, ChatResponse, TextPart, ToolCallPart, Usage
from coding_agent.cli import CliConfig, run_task
from agent.events import SubtaskFinished, SubtaskStarted
from coding_agent.session import SessionCompatibilityError, JsonlSessionStore


def response(*parts: TextPart | ToolCallPart, tokens: int = 5) -> ChatResponse:
    return ChatResponse(
        message=AssistantMessage(content=list(parts)),
        finish_reason="tool_calls" if any(isinstance(part, ToolCallPart) for part in parts) else "stop",
        usage=Usage(input_tokens=tokens - 1, output_tokens=1, total_tokens=tokens),
    )


def call(name: str, arguments: dict[str, str], call_id: str) -> ToolCallPart:
    return ToolCallPart(id=call_id, name=name, arguments_json=json.dumps(arguments))


def config(workspace: Path, *, max_steps: int = 6, session: bool = False) -> CliConfig:
    return CliConfig(
        task="Investigate and complete the task.",
        workspace=workspace,
        provider_id="scripted",
        model_id="test-model",
        base_url="http://example.invalid",
        api_key_env="UNUSED",
        system_prompt="You are a coding assistant.",
        max_steps=max_steps,
        session_path=workspace / "session.jsonl" if session else None,
        multi_agent=True,
    )


@pytest.mark.asyncio
async def test_delegate_is_read_only_and_usage_includes_child(tmp_path: Path) -> None:
    (tmp_path / "source.txt").write_text("source\n", encoding="utf-8")
    provider = ScriptedProvider([
        response(call("delegate_task", {"task": "Inspect source.txt"}, "delegate")),
        response(call("write_file", {"path": "forbidden.txt", "content": "bad"}, "bad")),
        response(call("read_file", {"path": "source.txt"}, "read")),
        response(TextPart(text="source.txt contains source")),
        response(TextPart(text="Done.")),
    ])
    events = []
    result = await run_task(config(tmp_path, session=True), provider, StringIO(), on_event=events.append)

    assert result.exit_code == 0
    assert not (tmp_path / "forbidden.txt").exists()
    assert result.run_result.steps == 5
    assert result.run_result.usage.total_tokens == 25
    assert len([event for event in events if isinstance(event, SubtaskStarted)]) == 1
    assert len([event for event in events if isinstance(event, SubtaskFinished)]) == 1
    assert "write_file" not in [tool.name for tool in provider.requests[1].tools]
    assert "delegate_task" not in [tool.name for tool in provider.requests[1].tools]
    assert provider.requests[-1].messages[-1].content == "source.txt contains source"
    history = JsonlSessionStore(tmp_path / "session.jsonl").load().messages
    assert len(history) == 4
    assert isinstance(history[-1], AssistantMessage)
    assert history[-1].content == [TextPart(text="Done.")]
    assert all(getattr(message, "tool_name", None) != "write_file" for message in history)
    child_records = [json.loads(line) for line in (
        tmp_path / "session.jsonl.subtasks.jsonl"
    ).read_text(encoding="utf-8").splitlines()]
    assert len(child_records) == 6
    started = next(event for event in events if isinstance(event, SubtaskStarted))
    assert {record["task_id"] for record in child_records} == {started.task_id}
    assert any(record["message"].get("tool_name") == "write_file" for record in child_records)


@pytest.mark.asyncio
async def test_delegate_shares_model_request_limit(tmp_path: Path) -> None:
    provider = ScriptedProvider([
        response(call("delegate_task", {"task": "Inspect files"}, "delegate")),
        response(TextPart(text="Found files.")),
    ])
    result = await run_task(config(tmp_path, max_steps=2), provider, StringIO())

    assert result.exit_code == 1
    assert result.run_result.failure is not None
    assert result.run_result.failure.code == "max_steps_exceeded"
    assert len(provider.requests) == 2


def test_multi_agent_session_cannot_resume_as_single(tmp_path: Path) -> None:
    from coding_agent.cli import prepare_session
    from ai.schemas import ModelSpec

    multi = config(tmp_path, session=True)
    model = ModelSpec(provider="scripted", id="test-model")
    prepare_session(multi, model)
    with pytest.raises(SessionCompatibilityError, match="multi-agent mode"):
        prepare_session(replace(multi, multi_agent=False), model)


@pytest.mark.asyncio
async def test_subtask_history_rejects_symlink(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("unchanged", encoding="utf-8")
    (tmp_path / "session.jsonl.subtasks.jsonl").symlink_to(outside)
    provider = ScriptedProvider([
        response(call("delegate_task", {"task": "Inspect files"}, "delegate")),
        response(TextPart(text="Findings.")),
        response(TextPart(text="Done.")),
    ])
    result = await run_task(config(tmp_path, session=True), provider, StringIO())

    assert result.exit_code == 0
    assert outside.read_text(encoding="utf-8") == "unchanged"
    from ai.schemas import ToolResultMessage
    history = JsonlSessionStore(tmp_path / "session.jsonl").load().messages
    tool_result = next(message for message in history if isinstance(message, ToolResultMessage))
    assert tool_result.is_error
    assert "outside the workspace" in tool_result.content
