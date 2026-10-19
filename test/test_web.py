from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import httpx
import pytest

from ai.openai_compatible import OpenAICompatibleProvider
from ai.provider import ProviderError, ScriptedProvider
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


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("LARIO_MEMORY_URL", raising=False)


@pytest.mark.asyncio
async def test_web_rejects_untrusted_unauthorized_and_cross_origin_requests(tmp_path):
    async with web(tmp_path) as (client, service, provider):
        assert (await client.get("/api/state", headers={"Authorization": "Bearer wrong"})).status_code == 401
        assert (await client.get("/api/state", headers={"Origin": "https://attacker.example"})).status_code == 403
        assert (await client.get("/api/state", headers={"Host": "attacker.example:8000"})).status_code == 403
        state = (await client.get("/api/state")).json()
        assert not state["trusted"]
        assert (await client.post("/api/tasks", json={"text": "read secrets", "session_id": service.active.id})).status_code == 403
        assert not provider.requests
        assert not (tmp_path / ".runtime").exists()


@pytest.mark.asyncio
async def test_web_write_requires_current_one_time_approval_and_persists_session(tmp_path):
    provider = ScriptedProvider([call("write_file", {"path": "hello.txt", "content": "hello"}), reply("文件已创建")])
    async with web(tmp_path, provider) as (client, service, _):
        await client.post("/api/trust", json={"trusted": True})
        await start(client)
        await wait_phase(service, "awaiting_edit")
        assert not (tmp_path / "hello.txt").exists()
        approval = service.approval.copy()
        assert "+hello" in approval["detail"]
        assert (await client.post("/api/sessions", json={})).status_code == 409
        assert (await client.post("/api/approvals/wrong", json={"approved": True, "run_id": approval["run_id"]})).status_code == 409
        assert (await client.post(f"/api/approvals/{approval['id']}", json={"approved": True, "run_id": "old-run"})).status_code == 409
        decision = {"approved": True, "run_id": approval["run_id"]}
        assert (await client.post(f"/api/approvals/{approval['id']}", json=decision)).status_code == 200
        assert (await client.post(f"/api/approvals/{approval['id']}", json=decision)).status_code == 409
        await wait_phase(service, "completed")
        assert (tmp_path / "hello.txt").read_text() == "hello"
        snapshot = JsonlSessionStore(service.path(service.active.path)).load()
        assert snapshot.runs[-1].status == "completed"
        assert snapshot.messages[-1].content[0].text == "文件已创建"
        assert service.active.tools[0]["status"] == "completed"
    async with web(tmp_path) as (client, service, _):
        await client.post("/api/trust", json={"trusted": True})
        assert service.active.messages[-1]["text"] == "文件已创建"
        assert service.active.phase == "completed"


@pytest.mark.asyncio
async def test_web_rejection_is_tool_error_returned_to_model(tmp_path):
    provider = ScriptedProvider([call("write_file", {"path": "no.txt", "content": "no"}), reply("尊重拒绝")])
    async with web(tmp_path, provider) as (client, service, _):
        await client.post("/api/trust", json={"trusted": True})
        await start(client)
        await wait_phase(service, "awaiting_edit")
        approval = service.approval
        await client.post(f"/api/approvals/{approval['id']}", json={"approved": False, "run_id": approval["run_id"]})
        await wait_phase(service, "completed")
        assert not (tmp_path / "no.txt").exists()
        result = provider.requests[-1].messages[-1]
        assert isinstance(result, ToolResultMessage) and result.is_error
        assert "rejected" in result.content


@pytest.mark.asyncio
async def test_web_command_uses_shared_docker_tool_and_displays_actual_exit(tmp_path, fake_docker):
    fake_docker.exit_code = 17
    provider = ScriptedProvider([call("run_command", {"argv": ["python", "-V"], "cwd": "."}), reply("命令返回非零退出码")])
    async with web(tmp_path, provider) as (client, service, _):
        await client.post("/api/trust", json={"trusted": True})
        await start(client)
        await wait_phase(service, "awaiting_command")
        approval = service.approval
        assert "python -V" in approval["detail"] and str(tmp_path) in approval["detail"]
        assert not fake_docker.calls
        await client.post(f"/api/approvals/{approval['id']}", json={"approved": True, "run_id": approval["run_id"]})
        await wait_phase(service, "completed")
        assert "exit_code: 17" in service.active.tools[-1]["output"]
        assert [entry[0] for entry in fake_docker.calls] == ["create", "start", "inspect", "rm"]


@pytest.mark.asyncio
async def test_stop_invalidates_approval_and_recovery_never_replays_write(tmp_path):
    provider = ScriptedProvider([call("write_file", {"path": "unknown.txt", "content": "no"}), reply()])
    async with web(tmp_path, provider) as (client, service, _):
        await client.post("/api/trust", json={"trusted": True})
        await start(client)
        await wait_phase(service, "awaiting_edit")
        approval = service.approval.copy()
        assert (await client.post("/api/stop")).status_code == 200
        assert service.active.phase == "cancelled"
        assert (await client.post(f"/api/approvals/{approval['id']}", json={"approved": True, "run_id": approval["run_id"]})).status_code == 409
        assert not (tmp_path / "unknown.txt").exists()
    resumed = ScriptedProvider([reply("已检查中断")])
    async with web(tmp_path, resumed) as (client, service, _):
        await client.post("/api/trust", json={"trusted": True})
        await start(client)
        await wait_phase(service, "completed")
        messages = resumed.requests[0].messages
        assert any(isinstance(m, ToolResultMessage) and m.is_error and "unknown" in m.content for m in messages)
        assert not (tmp_path / "unknown.txt").exists()


@pytest.mark.asyncio
async def test_settings_share_tui_file_and_never_store_key(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_MODEL_KEY", "private-test-value")
    async with web(tmp_path) as (client, service, _):
        await client.post("/api/trust", json={"trusted": True})
        settings = service.snapshot()["settings"]
        settings.update(model_id="another-model", api_key_env="TEST_MODEL_KEY", context_window=8192, multi_agent=True)
        result = await client.post("/api/settings", json=settings)
        assert result.status_code == 200, result.text
        saved = parse_interface_args(["--workspace", str(tmp_path)])
        assert saved.model_id == "another-model"
        assert saved.multi_agent and saved.context_window == 8192
        assert result.json()["api_key_set"] is True
        assert "private-test-value" not in result.text
        assert "private-test-value" not in (tmp_path / ".runtime/lario.json").read_text()
        for invalid in ("../outside.jsonl", str(tmp_path / "absolute.jsonl")):
            assert (await client.post("/api/sessions", json={"path": invalid})).status_code == 422
        assert (await client.post("/api/settings", json={**settings, "base_url": "https://user:secret@example.org"})).status_code == 422
        assert (await client.post("/api/tasks", json={"text": "x" * 8001, "session_id": service.active.id})).status_code == 422


@pytest.mark.asyncio
async def test_sessions_remain_separate_and_model_mismatch_fails_before_request(tmp_path):
    provider = ScriptedProvider([reply("第一条"), reply("第二条")])
    async with web(tmp_path, provider) as (client, service, _):
        await client.post("/api/trust", json={"trusted": True})
        await start(client)
        await wait_phase(service, "completed")
        old = service.active.id
        await client.post("/api/sessions", json={})
        assert not service.active.messages
        await start(client)
        await wait_phase(service, "completed")
        assert len(provider.requests[1].messages) == 1
        await client.post(f"/api/sessions/{old}/select")
        assert service.active.messages[-1]["text"] == "第一条"
        settings = service.snapshot()["settings"]
        await client.post("/api/settings", json={**settings, "model_id": "different"})
        result = await client.post("/api/tasks", json={"text": "继续", "session_id": old})
        assert result.status_code == 422
        assert "configuration differs" in result.text
        assert len(provider.requests) == 2


@pytest.mark.asyncio
async def test_streamed_provider_text_and_partial_cancellation(tmp_path):
    emitted = asyncio.Event()
    class StreamingProvider(ScriptedProvider):
        async def complete_stream(self, model, request, on_text):
            on_text("部分回复")
            emitted.set()
            await asyncio.Event().wait()
    async with web(tmp_path, StreamingProvider([])) as (client, service, _):
        await client.post("/api/trust", json={"trusted": True})
        stream = service.stream()
        assert '"trusted": true' in await anext(stream)
        await start(client)
        await emitted.wait()
        frame = await anext(stream)
        assert '部分回复' in frame
        await client.post("/api/stop")
        assert service.active.messages[-1] == {"role": "partial", "text": "部分回复"}
        assert "部分回复" not in service.path(service.active.path).read_text(encoding="utf-8")
        await stream.aclose()


@pytest.mark.asyncio
async def test_web_real_openai_adapter_with_offline_sse_transport(tmp_path):
    seen = []
    def respond(request):
        seen.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer private-test-key"
        data = {"choices": [{"delta": {"content": "真实适配器链路"}, "finish_reason": None}]}
        end = {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}}
        return httpx.Response(200, text=f"data: {json.dumps(data)}\n\ndata: {json.dumps(end)}\n\ndata: [DONE]\n\n", headers={"content-type": "text/event-stream"})
    provider = OpenAICompatibleProvider(provider_id="openai", base_url="https://offline.test/v1", api_key="private-test-key", transport=httpx.MockTransport(respond))
    config = parse_interface_args(["--workspace", str(tmp_path), "--model", "test-model"])
    async with web(tmp_path, provider, config=config) as (client, service, _):
        await client.post("/api/trust", json={"trusted": True})
        await start(client)
        await wait_phase(service, "completed")
        assert service.active.messages[-1]["text"] == "真实适配器链路"
        assert service.active.usage["total_tokens"] == 8
        assert seen[0]["stream"] is True
        assert {t["function"]["name"] for t in seen[0]["tools"]} >= {"read_file", "edit_file", "run_command"}
        assert "private-test-key" not in (await client.get("/api/state")).text


@pytest.mark.asyncio
async def test_last_browser_disconnect_cancels_run_and_cleanup(tmp_path, monkeypatch):
    provider = ScriptedProvider([call("write_file", {"path": "never.txt", "content": "no"})])
    async with web(tmp_path, provider) as (client, service, _):
        await client.post("/api/trust", json={"trusted": True})
        await start(client)
        await wait_phase(service, "awaiting_edit")
        real_sleep = asyncio.sleep
        async def short_grace(seconds):
            await real_sleep(0.01 if seconds == 20 else seconds)
        monkeypatch.setattr("coding_agent.web.asyncio.sleep", short_grace)
        service.arm_disconnect()
        await wait_phase(service, "cancelled")
        assert service.approval is None
        assert not (tmp_path / "never.txt").exists()


@pytest.mark.asyncio
async def test_large_write_arguments_still_show_small_review_and_full_target_path(tmp_path):
    content = "original\n" + "".join(f"unchanged line {i}\n" for i in range(3000))
    (tmp_path / "large.txt").write_bytes(content.encode())
    provider = ScriptedProvider([call("write_file", {"path": "large.txt", "content": content.replace("original", "updated", 1)}), reply()])
    async with web(tmp_path, provider) as (client, service, _):
        await client.post("/api/trust", json={"trusted": True})
        await start(client)
        await wait_phase(service, "awaiting_edit")
        assert service.approval["path"] == "large.txt"
        assert "+updated" in service.approval["detail"]
        await client.post("/api/stop")
        assert (tmp_path / "large.txt").read_text(encoding="utf-8") == content


@pytest.mark.asyncio
async def test_web_multi_agent_uses_shared_bounded_delegate_and_saves_child_history(tmp_path):
    config = parse_interface_args(["--workspace", str(tmp_path), "--provider-id", "scripted", "--model", "test-model", "--multi-agent"])
    provider = ScriptedProvider([call("delegate_task", {"task": "检查工作区"}), reply("子任务发现"), reply("已汇总发现")])
    async with web(tmp_path, provider, config=config) as (client, service, _):
        await client.post("/api/trust", json={"trusted": True})
        await client.post("/api/sessions", json={})
        session_id = service.active.id
        await start(client)
        await wait_phase(service, "completed")
        assert service.active.messages[-1]["text"] == "已汇总发现"
        assert service.active.steps == 3
        assert "子任务结束" in service.active.notices
        child_path = service.path(service.active.path + ".subtasks.jsonl")
        assert "子任务发现" in child_path.read_text(encoding="utf-8")
        assert {tool.name for tool in provider.requests[1].tools} == {"read_file", "list_dir", "glob_file", "grep_file"}
    async with web(tmp_path, config=parse_interface_args(["--workspace", str(tmp_path)])) as (client, service, _):
        await client.post("/api/trust", json={"trusted": True})
        assert service.active.id == session_id
        assert service.active.messages[-1]["text"] == "已汇总发现"
        assert len(service.sessions) == 1


@pytest.mark.asyncio
async def test_provider_failure_is_visible_and_next_turn_can_resume(tmp_path):
    class FailingProvider(ScriptedProvider):
        async def complete(self, model, request):
            raise ProviderError("模型连接失败", status_code=401)
    async with web(tmp_path, FailingProvider([])) as (client, service, _):
        await client.post("/api/trust", json={"trusted": True})
        await start(client)
        await wait_phase(service, "failed")
        assert "provider_error: 模型连接失败" == service.active.failure
        assert service.task is None
        assert JsonlSessionStore(service.path(service.active.path)).load().runs[-1].status == "failed"
        provider = ScriptedProvider([reply("连接恢复")])
        service.provider_factory = lambda _: provider
        await start(client)
        await wait_phase(service, "completed")
        assert service.active.messages[-1]["text"] == "连接恢复"


@pytest.mark.asyncio
async def test_web_reconnect_does_not_replay_pending_approval(tmp_path):
    provider = ScriptedProvider([call("write_file", {"path": "pending.txt", "content": "no"})])
    async with web(tmp_path, provider) as (client, service, _):
        await client.post("/api/trust", json={"trusted": True})
        first = service.stream()
        await anext(first)
        await start(client)
        await wait_phase(service, "awaiting_edit")
        approval = service.approval.copy()
        await first.aclose()
        second = service.stream()
        frame = await anext(second)
        assert approval["id"] in frame
        assert len(provider.requests) == 1
        assert not (tmp_path / "pending.txt").exists()
        await client.post("/api/stop")
        await second.aclose()
