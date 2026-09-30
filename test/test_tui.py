from __future__ import annotations

import asyncio
from io import StringIO
from pathlib import Path

import pytest

from ai.provider import ScriptedProvider
from ai.schemas import AssistantMessage, ChatResponse, TextPart, ToolCallPart
from coding_agent.cli import parse_args, run_task
from coding_agent.session import JsonlSessionStore
from coding_agent.tui import create_app, parse_tui_args


def log_text(line: object) -> str:
    return "".join(segment.text for segment in line._segments)


def test_parse_tui_args_uses_workspace_scoped_session(tmp_path: Path) -> None:
    config = parse_tui_args([
        "--workspace", str(tmp_path), "--model", "test-model",
    ])

    assert config.task == ""
    assert config.workspace == tmp_path.resolve()
    assert config.session_path == (
        tmp_path / ".runtime" / "session.jsonl"
    ).resolve()
    assert config.session_path.parent.is_dir()


class BlockingProvider:
    id = "scripted"

    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def complete(self, model, request):
        self.started.set()
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_cancelled_run_records_interrupted_session(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provider = BlockingProvider()
    config = parse_args([
        "stop", "--workspace", str(workspace),
        "--provider-id", "scripted", "--model", "test-model",
        "--session", "history.jsonl",
    ])

    task = asyncio.create_task(run_task(config, provider, StringIO()))
    await provider.started.wait()
    task.cancel()
    result = await task

    assert result.exit_code == 1
    assert result.run_result.failure is not None
    assert result.run_result.failure.code == "cancelled"
    snapshot = JsonlSessionStore(workspace / "history.jsonl").load()
    assert snapshot.runs[-1].status == "interrupted"


@pytest.mark.asyncio
async def test_tui_runs_task_and_renders_answer(tmp_path: Path) -> None:
    provider = ScriptedProvider([
        ChatResponse(
            message=AssistantMessage(content=[TextPart(text="Done")]),
            finish_reason="stop",
        )
    ])
    config = parse_tui_args([
        "--workspace", str(tmp_path), "--provider-id", "scripted",
        "--model", "test-model", "--session", "history.jsonl",
    ])
    app = create_app(config, provider=provider)

    async with app.run_test() as pilot:
        assert app.title == "Lario"
        assert app.query_one("#welcome").display is True
        assert app.query_one("#log").display is False
        assert app.query_one("#toggle-steps").label == "Show steps"
        assert app.query_one("#stop").label == "Stop"
        assert app.query_one("#task").region.bottom == app.query_one("Footer").region.y
        assert app.query_one("#task").styles.background.a == 0
        assert app.query_one("#toggle-steps").styles.border.bottom[0] == "round"
        await pilot.click("#task")
        await pilot.press("h", "i", "enter")
        await pilot.pause(0.1)

        assert len(provider.requests) == 1
        assert app.query_one("#welcome").display is False
        assert app.query_one("#log").display is True
        assert str(app.query_one("#status").render()).endswith("| ready")
        lines = app.query_one("#log").lines
        assert any("answer:" in log_text(line) for line in lines)
        assert any("Done" in log_text(line) for line in lines)
        assert all("##" not in log_text(line) for line in lines)


@pytest.mark.asyncio
async def test_tui_hides_steps_until_requested(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("hello\n", encoding="utf-8")
    provider = ScriptedProvider([
        ChatResponse(
            message=AssistantMessage(content=[ToolCallPart(
                id="read-1",
                name="read_file",
                arguments_json='{"path":"note.txt"}',
            )]),
            finish_reason="tool_calls",
        ),
        ChatResponse(
            message=AssistantMessage(content=[TextPart(text="## Done")]),
            finish_reason="stop",
        ),
    ])
    config = parse_tui_args([
        "--workspace", str(tmp_path), "--provider-id", "scripted",
        "--model", "test-model", "--session", "history.jsonl",
    ])
    app = create_app(config, provider=provider)

    async with app.run_test() as pilot:
        await pilot.click("#task")
        await pilot.press("h", "i", "enter")
        await pilot.pause(0.2)

        assert not any(
            "step 1:" in log_text(line)
            for line in app.query_one("#log").lines
        )
        await pilot.click("#toggle-steps")
        assert any(
            "step 1:" in log_text(line)
            for line in app.query_one("#log").lines
        )
        await pilot.click("#toggle-steps")
        assert app.query_one("#toggle-steps").label == "Show steps"
        assert not any(
            "step 1:" in log_text(line)
            for line in app.query_one("#log").lines
        )


@pytest.mark.asyncio
async def test_tui_submits_multiline_task(tmp_path: Path) -> None:
    provider = ScriptedProvider([
        ChatResponse(
            message=AssistantMessage(content=[TextPart(text="Done")]),
            finish_reason="stop",
        )
    ])
    config = parse_tui_args([
        "--workspace", str(tmp_path), "--provider-id", "scripted",
        "--model", "test-model", "--session", "history.jsonl",
    ])
    app = create_app(config, provider=provider)

    async with app.run_test() as pilot:
        task_input = app.query_one("#task")
        task_input.load_text("first line")
        task_input.move_cursor((0, len("first line")))
        await pilot.press("shift+enter")
        task_input.insert("second line")
        await pilot.press("enter")
        await pilot.pause(0.1)

        assert provider.requests[0].messages[-1].content == (
            "first line\nsecond line"
        )


@pytest.mark.asyncio
async def test_tui_rejects_edit_without_changing_file(tmp_path: Path) -> None:
    target = tmp_path / "module.py"
    target.write_text("value = 1\n", encoding="utf-8")
    provider = ScriptedProvider([
        ChatResponse(
            message=AssistantMessage(content=[ToolCallPart(
                id="edit-1",
                name="edit_file",
                arguments_json=(
                    '{"path":"module.py","old_string":"value = 1",'
                    '"new_string":"value = 2"}'
                ),
            )]),
            finish_reason="tool_calls",
        ),
        ChatResponse(
            message=AssistantMessage(content=[TextPart(text="Rejected")]),
            finish_reason="stop",
        ),
    ])
    config = parse_tui_args([
        "--workspace", str(tmp_path), "--provider-id", "scripted",
        "--model", "test-model", "--session", "history.jsonl",
    ])
    app = create_app(config, provider=provider)

    async with app.run_test() as pilot:
        await pilot.click("#task")
        await pilot.press("f", "i", "x", "enter")
        await pilot.pause(0.2)
        assert app.screen.query_one("#approval-dialog")
        await pilot.click("#reject")
        await pilot.pause(0.1)

    assert target.read_text(encoding="utf-8") == "value = 1\n"
