from __future__ import annotations

import json
from io import StringIO

import httpx
import pytest

from ai.provider import ScriptedProvider
from ai.schemas import AssistantMessage, ChatResponse, TextPart
from agent.loop import RequestPreparationError
from coding_agent.cli import CliConfig, run_task
from coding_agent.compaction import RequestBudgetManager
from coding_agent.memory import MemoryCoreClient
from coding_agent.session import JsonlSessionStore


@pytest.mark.asyncio
async def test_memory_recall_and_capture_use_one_session_without_changing_history(tmp_path):
    seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/atomic/search"):
            return httpx.Response(200, json={"code": 0, "data": {
                "items": [{"content": "Use the existing parser."}],
            }})
        return httpx.Response(200, json={"code": 0, "data": {
            "accepted_ids": ["1", "2"], "total_count": 2,
        }})

    memory = MemoryCoreClient(
        endpoint="http://memory.test/", service_id="default", team_id="team",
        agent_id="agent", user_id="user", api_key="test-key",
        transport=httpx.MockTransport(handle),
    )
    config = CliConfig(
        task="Fix the parser", workspace=tmp_path, provider_id="scripted",
        model_id="test-model", base_url="http://model.test", api_key_env="TEST_KEY",
        system_prompt="You are a coding assistant.", max_steps=2,
        session_path=tmp_path / "session.jsonl", context_window=4096,
    )
    provider = ScriptedProvider([ChatResponse(
        message=AssistantMessage(content=[TextPart(text="Parser fixed.")]),
        finish_reason="stop",
    )])

    result = await run_task(config, provider, StringIO(), memory=memory)

    assert result.exit_code == 0
    assert "Use the existing parser." in provider.requests[0].system_prompt
    assert len(seen) == 2
    assert [request.url.path for request in seen] == [
        "/v3/atomic/search", "/v3/conversation/add",
    ]
    assert all(request.headers["x-tdai-service-id"] == "default" for request in seen)
    assert all(request.headers["authorization"] == "Bearer test-key" for request in seen)
    body = json.loads(seen[1].content)
    assert body["team_id"] == "team"
    assert body["agent_id"] == "agent"
    assert body["user_id"] == "user"
    assert body["messages"] == [
        {"role": "user", "content": "Fix the parser"},
        {"role": "assistant", "content": "Parser fixed."},
    ]
    snapshot = JsonlSessionStore(config.session_path).load()
    assert body["session_id"] == snapshot.metadata.session_id
    assert "Use the existing parser." not in config.session_path.read_text()


@pytest.mark.asyncio
async def test_memory_failure_does_not_fail_task(tmp_path):
    memory = MemoryCoreClient(
        endpoint="http://memory.test/", service_id="default", team_id="team",
        agent_id="agent", user_id="user", api_key="test-key",
        transport=httpx.MockTransport(lambda _: httpx.Response(503)),
    )
    config = CliConfig(
        task="Say hi", workspace=tmp_path, provider_id="scripted",
        model_id="test-model", base_url="http://model.test", api_key_env="TEST_KEY",
        system_prompt="You are a coding assistant.", max_steps=2,
        session_path=tmp_path / "session.jsonl",
    )
    provider = ScriptedProvider([ChatResponse(
        message=AssistantMessage(content=[TextPart(text="Hi.")]),
        finish_reason="stop",
    )])
    output = StringIO()

    result = await run_task(config, provider, output, memory=memory)

    assert result.exit_code == 0
    assert "memory recall unavailable" in output.getvalue()
    assert "memory capture unavailable" in output.getvalue()
    assert provider.requests[0].system_prompt == config.system_prompt


@pytest.mark.asyncio
async def test_memory_is_omitted_when_it_exceeds_context_budget(tmp_path, monkeypatch):
    memory = MemoryCoreClient(
        endpoint="http://memory.test/", service_id="default", team_id="team",
        agent_id="agent", user_id="user", api_key="test-key",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
            "code": 0, "data": {"items": [{"content": "Older preference"}]}
            if request.url.path.endswith("/atomic/search") else {},
        })),
    )
    config = CliConfig(
        task="Say hi", workspace=tmp_path, provider_id="scripted",
        model_id="test-model", base_url="http://model.test", api_key_env="TEST_KEY",
        system_prompt="You are a coding assistant.", max_steps=2,
        session_path=tmp_path / "session.jsonl", context_window=4096,
    )
    provider = ScriptedProvider([ChatResponse(
        message=AssistantMessage(content=[TextPart(text="Hi.")]),
        finish_reason="stop",
    )])
    original_prepare = RequestBudgetManager.prepare

    async def prepare(self, state):
        if "Older preference" in state.system_prompt:
            raise RequestPreparationError("context_budget_exceeded", "memory too large")
        await original_prepare(self, state)

    monkeypatch.setattr(RequestBudgetManager, "prepare", prepare)
    output = StringIO()

    result = await run_task(config, provider, output, memory=memory)

    assert result.exit_code == 0
    assert provider.requests[0].system_prompt == config.system_prompt
    assert "memory omitted: context budget" in output.getvalue()
