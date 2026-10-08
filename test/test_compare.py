from __future__ import annotations

import json

import pytest

from ai.provider import ScriptedProvider
from ai.schemas import AssistantMessage, ChatResponse, TextPart, ToolCallPart, Usage
from coding_agent.cli import CliConfig
from evals.cases.model import EvalCase
from evals.compare import run_comparison


def answer(*parts: TextPart | ToolCallPart) -> ChatResponse:
    return ChatResponse(
        message=AssistantMessage(content=list(parts)),
        finish_reason="tool_calls" if any(isinstance(p, ToolCallPart) for p in parts) else "stop",
        usage=Usage(input_tokens=4, output_tokens=1, total_tokens=5),
    )


@pytest.mark.asyncio
async def test_comparison_reports_paired_success_and_all_tokens() -> None:
    case = EvalCase(
        case_id="paired",
        task="Write result.txt with good content.",
        expected_files={"result.txt": "good"},
        expected_tool_names=frozenset({"write_file"}),
    )

    def provider_factory(config: CliConfig) -> ScriptedProvider:
        if not config.multi_agent:
            return ScriptedProvider([answer(TextPart(text="Done without writing."))])
        return ScriptedProvider([
            answer(ToolCallPart(id="delegate", name="delegate_task", arguments_json=json.dumps({"task": "Check task"}))),
            answer(TextPart(text="Write good content.")),
            answer(ToolCallPart(id="write", name="write_file", arguments_json=json.dumps({"path": "result.txt", "content": "good"}))),
            answer(TextPart(text="Done.")),
        ])

    comparison = await run_comparison(provider_factory, cases=[case])
    summary = comparison.summary()
    assert summary["single_only_successes"] == 0
    assert summary["multi_only_successes"] == 1
    assert summary["multi"]["passed"] == 1
    assert summary["multi"]["total_tokens"] == 20
    assert summary["multi"]["subtasks"] == 1
