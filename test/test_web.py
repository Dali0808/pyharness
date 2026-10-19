from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import httpx
import pytest

from ai.openai_compatible import OpenAICompatibleProvider
from ai.provider import ScriptedProvider
from ai.schemas import AssistantMessage, ChatResponse, TextPart, ToolCallPart, ToolResultMessage
from coding_agent.session import JsonlSessionStore
from coding_agent.settings import parse_interface_args
from coding_agent.web import create_web_app


def reply(text="完成"):
    return ChatResponse(message=AssistantMessage(content=[TextPart(text=text)]), finish_reason="stop")


def call(name, arguments, call_id="call-1"):
    return ChatResponse(message=AssistantMessage(content=[ToolCallPart(id=call_id, name=name, arguments_json=json.dumps(arguments))]), finish_reason="tool_calls")


@asynccontextmanager
async def web(tmp_path, provider=None, *, config=None):
    config = config or parse_interface_args(["--workspace", str(tmp_path), "--provider-id", "scripted", "--model", "test-model"])
    provider = provider or ScriptedProvider([reply()])
    app = create_web_app(config, token="test-token", provider_factory=lambda _: provider)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000", headers={"Authorization": "Bearer test-token"}) as client:
            yield client, app.state.service, provider


async def wait_phase(service, phase):
    async with asyncio.timeout(3):
        while service.active.phase != phase or (phase in {"completed", "cancelled", "failed"} and service.task is not None):
            await asyncio.sleep(0.005)


async def start(client):
    state = (await client.get("/api/state")).json()
    response = await client.post("/api/tasks", json={"text": "请完成任务", "session_id": state["active"]["id"]})
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_web_rejects_untrusted_unauthorized_and_cross_origin_requests(tmp_path):
    async with web(tmp_path) as (client, service, provider):
        assert (await client.get("/api/state", headers={"Authorization": "Bearer wrong"})).status_code == 401
        assert (await client.get("/api/state", headers={"Origin": "https://attacker.example"})).status_code == 403
        assert (await client.get("/api/state", headers={"Host": "attacker.example:8000"})).status_code == 403
        assert (await client.post("/api/tasks", json={"text": "read secrets", "session_id": service.active.id})).status_code == 403
        assert not provider.requests


@pytest.mark.asyncio
async def test_web_reads_uploaded_document_and_creates_downloadable_artifact(tmp_path):
    provider = ScriptedProvider([call("read_file", {"path": "README.md"}), call("create_artifact", {"name": "report.md", "content": "# 项目分析"}, "out"), reply("报告已生成")])
    async with web(tmp_path, provider) as (client, service, _):
        await client.post("/api/trust", json={"trusted": True})
        assert (await client.post("/api/files/documents/README.md", content="# Example")).status_code == 200
        await start(client)
        await wait_phase(service, "completed")
        assert provider.requests[1].messages[-1].content == "# Example"
        result = await client.get("/api/files/artifacts/report.md")
        assert result.text == "# 项目分析"
        assert "attachment" in result.headers["content-disposition"]
        assert result.headers["x-content-type-options"] == "nosniff"
        assert not (tmp_path / "report.md").exists()
        assert service.snapshot()["files"]["artifacts"][0]["name"] == "report.md"
        assert JsonlSessionStore(service.path(service.active.path)).load().runs[-1].status == "completed"
    async with web(tmp_path) as (client, service, _):
        await client.post("/api/trust", json={"trusted": True})
        assert service.active.messages[-1]["text"] == "报告已生成"


@pytest.mark.parametrize("name,args", [
    ("write_file", {"path": "hacked.txt", "content": "no"}),
    ("edit_file", {"path": "source.txt", "old_string": "safe", "new_string": "hacked"}),
    ("run_command", {"argv": ["python", "-V"]}),
])
@pytest.mark.asyncio
async def test_web_never_registers_local_coding_tools_even_when_model_requests_them(tmp_path, fake_docker, name, args):
    (tmp_path / "source.txt").write_text("safe")
    provider = ScriptedProvider([call(name, args), reply("没有该权限")])
    async with web(tmp_path, provider) as (client, service, _):
        await client.post("/api/trust", json={"trusted": True})
        await start(client)
        await wait_phase(service, "completed")
        assert {tool.name for tool in provider.requests[0].tools} == {"read_file", "list_dir", "glob_file", "grep_file", "create_artifact"}
        error = provider.requests[1].messages[-1]
        assert isinstance(error, ToolResultMessage) and error.is_error and "unknown_tool" in error.content
        assert not (tmp_path / "hacked.txt").exists()
        assert (tmp_path / "source.txt").read_text() == "safe"
        assert not fake_docker.calls
        assert (await client.post("/api/approvals/any", json={"approved": True, "run_id": "any"})).status_code == 404


@pytest.mark.asyncio
async def test_web_cannot_read_host_files_session_history_or_shared_memory(tmp_path, monkeypatch):
    (tmp_path / "secret.txt").write_text("host secret")
    monkeypatch.setenv("LARIO_MEMORY_URL", "http://must-not-contact.invalid")
    def forbidden():
        pytest.fail("Web must not initialize shared TUI memory")
    monkeypatch.setattr("coding_agent.cli.MemoryCoreClient.from_env", forbidden)
    provider = ScriptedProvider([call("read_file", {"path": "../sessions/default.jsonl"}), call("read_file", {"path": "secret.txt"}, "second"), reply()])
    async with web(tmp_path, provider) as (client, service, _):
        await client.post("/api/trust", json={"trusted": True})
        await start(client)
        await wait_phase(service, "completed")
        assert all(provider.requests[index].messages[-1].is_error for index in (1, 2))
        assert "host secret" not in json.dumps(service.snapshot())
        assert (await client.post("/api/settings", json={"base_url": "https://attacker.invalid"})).status_code == 404
        assert (await client.post("/api/sessions", json={"path": "outside.jsonl"})).status_code == 422


@pytest.mark.asyncio
async def test_web_rejects_binary_oversize_overwrite_and_can_delete_files_and_sessions(tmp_path):
    async with web(tmp_path) as (client, service, _):
        await client.post("/api/trust", json={"trusted": True})
        assert (await client.post("/api/files/documents/data.txt", content=b"\x00bad")).status_code == 422
        assert (await client.post("/api/files/documents/data.exe", content="text")).status_code == 422
        assert (await client.post("/api/files/documents/data.txt", content=b"x" * 1_000_001)).status_code == 413
        assert (await client.post("/api/files/documents/data.txt", content="first")).status_code == 200
        assert (await client.post("/api/files/documents/data.txt", content="second")).status_code == 409
        assert (await client.get("/api/files/documents/data.txt")).text == "first"
        assert (await client.delete("/api/files/documents/data.txt")).status_code == 200
        assert (await client.get("/api/files/documents/data.txt")).status_code == 404
        await start(client)
        await wait_phase(service, "completed")
        path = service.path(service.active.path)
        assert path.exists()
        response = await client.delete(f"/api/sessions/{service.active.id}")
        assert response.status_code == 200 and not path.exists()
        assert response.json()["active"]["messages"] == []


@pytest.mark.asyncio
async def test_web_stream_stop_and_reconnect_never_replays_model_request(tmp_path):
    class SlowProvider(ScriptedProvider):
        async def complete_stream(self, model, request, on_text):
            self.requests.append(request)
            on_text("未完成文本")
            await asyncio.Event().wait()
    provider = SlowProvider([])
    async with web(tmp_path, provider) as (client, service, _):
        await client.post("/api/trust", json={"trusted": True})
        first = service.stream()
        await anext(first)
        await start(client)
        async with asyncio.timeout(2):
            while not service.active.live:
                await asyncio.sleep(0.005)
        await first.aclose()
        second = service.stream()
        assert "未完成文本" in await anext(second)
        assert len(provider.requests) == 1
        assert (await client.post("/api/stop")).status_code == 200
        assert service.active.phase == "cancelled"
        assert service.active.messages[-1]["role"] == "partial"
        snapshot = JsonlSessionStore(service.path(service.active.path)).load()
        assert all(not isinstance(message, AssistantMessage) for message in snapshot.messages)
        await second.aclose()


@pytest.mark.asyncio
async def test_web_real_openai_adapter_offline_sse_and_tool_allowlist(tmp_path):
    def handle(request):
        payload = json.loads(request.content)
        assert {tool["function"]["name"] for tool in payload["tools"]} == {"read_file", "list_dir", "glob_file", "grep_file", "create_artifact"}
        chunks = [
            {"choices": [{"delta": {"content": "真实适配器链路"}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}},
        ]
        data = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, text=data, headers={"content-type": "text/event-stream"})
    provider = OpenAICompatibleProvider(provider_id="scripted", base_url="https://offline.invalid/v1", api_key="not-a-real-key", transport=httpx.MockTransport(handle))
    async with web(tmp_path, provider) as (client, service, _):
        await client.post("/api/trust", json={"trusted": True})
        await start(client)
        await wait_phase(service, "completed")
        assert service.active.messages[-1]["text"] == "真实适配器链路"
        assert service.active.usage["total_tokens"] == 8
