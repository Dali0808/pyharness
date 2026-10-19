from __future__ import annotations

import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from ai.provider import ProviderError, ScriptedProvider
from ai.schemas import ChatRequest, ModelSpec, Usage
from coding_agent.accounts import Accounts, MeteredProvider
from coding_agent.files import FileLimits, FileStore
from coding_agent.server import COOKIE, ServerConfig, create_server_app
from test.test_web import call, reply, start, wait_phase

PASSWORD = "test-only-passphrase-2026"
ORIGIN = "https://assistant.example"


async def login(client, username="alice", password=PASSWORD):
    return await client.post("/api/auth/login", json={"username": username, "password": password})


def client_for(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN, headers={"Origin": ORIGIN})


@pytest.mark.asyncio
async def test_login_csrf_logout_password_reset_and_expiry(tmp_path):
    app = create_server_app(ServerConfig(tmp_path, public_url=ORIGIN))
    accounts = app.state.accounts
    user_id = accounts.create_user("alice", PASSWORD)
    async with app.router.lifespan_context(app), client_for(app) as client:
        assert (await client.get("/api/state")).status_code == 401
        assert (await client.post("/api/auth/login", headers={"Origin": "https://other.example"}, json={"username": "alice", "password": PASSWORD})).status_code == 403
        response = await login(client)
        assert response.status_code == 200
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie and "secure" in cookie and "samesite=strict" in cookie
        state = (await client.get("/api/state")).json()
        assert state["account"]["username"] == "alice"
        assert PASSWORD not in json.dumps(state) and "password" not in json.dumps(state)
        assert (await client.post("/api/settings", json={"base_url": "https://other.example"})).status_code == 404
        assert (await client.post("/api/sessions", headers={"Origin": ""}, json={})).status_code == 403
        assert (await client.post("/api/auth/logout")).status_code == 200
        assert (await client.get("/api/state")).status_code == 401
        await login(client)
        accounts.reset_password("alice", PASSWORD + "new")
        assert (await client.get("/api/state")).status_code == 401
        assert (await login(client)).status_code == 401
        assert (await login(client, password=PASSWORD + "new")).status_code == 200
        with accounts.connect() as db:
            db.execute("UPDATE logins SET expires=0 WHERE user_id=?", (user_id,))
        assert (await client.get("/api/state")).status_code == 401


@pytest.mark.asyncio
async def test_two_users_have_isolated_files_sessions_models_and_downloads(tmp_path):
    def factory(config):
        return ScriptedProvider([call("create_artifact", {"name": "report.md", "content": config.task}), reply("完成")])
    app = create_server_app(ServerConfig(tmp_path, public_url=ORIGIN, model="test", provider_id="scripted"), provider_factory=factory)
    alice_id = app.state.accounts.create_user("alice", PASSWORD)
    bob_id = app.state.accounts.create_user("bob", PASSWORD)
    async with app.router.lifespan_context(app), client_for(app) as alice, client_for(app) as bob:
        await login(alice)
        await login(bob, "bob")
        await alice.post("/api/files/documents/secret.txt", content="alice only")
        alice_state = (await alice.get("/api/state")).json()
        assert (await bob.get("/api/state")).json()["files"]["documents"] == []
        assert (await bob.get("/api/files/documents/secret.txt")).status_code == 404
        await alice.post("/api/sessions", json={})
        selected = (await alice.get("/api/state")).json()["active"]["id"]
        assert (await bob.post(f"/api/sessions/{selected}/select", json={})).status_code == 404
        await start(alice)
        await wait_phase(app.state.services[alice_id], "completed")
        assert (await bob.get("/api/files/artifacts/report.md")).status_code == 404
        assert (await alice.get("/api/files/artifacts/report.md")).text == "请完成任务"
        assert app.state.accounts.usage(alice_id)["calls"] == 2
        assert app.state.accounts.usage(bob_id)["calls"] == 0
        assert alice_state["settings"] == {"model_id": "test"}
        app.state.accounts.disable("alice")
        assert (await alice.get("/api/state")).status_code == 401


@pytest.mark.asyncio
async def test_daily_request_limit_blocks_next_model_call_and_survives_restart(tmp_path):
    provider = ScriptedProvider([call("list_dir", {"path": "."}), reply("must not reach")])
    config = ServerConfig(tmp_path, public_url=ORIGIN, model="test", provider_id="scripted", daily_requests=1)
    app = create_server_app(config, provider_factory=lambda _: provider)
    user_id = app.state.accounts.create_user("alice", PASSWORD)
    async with app.router.lifespan_context(app), client_for(app) as client:
        await login(client)
        await start(client)
        await wait_phase(app.state.services[user_id], "failed")
        assert len(provider.requests) == 1
        assert provider.requests[0].max_tokens == 4096
        assert "quota exceeded" in app.state.services[user_id].active.failure
        assert app.state.accounts.usage(user_id)["calls"] == 1
    assert create_server_app(config).state.accounts.usage(user_id)["calls"] == 1


@pytest.mark.asyncio
async def test_file_limits_streaming_upload_and_maintenance(tmp_path):
    config = ServerConfig(tmp_path, public_url=ORIGIN, model="test", file_limits=FileLimits(max_file_bytes=10, max_storage_bytes=15, max_files=2))
    app = create_server_app(config)
    app.state.accounts.create_user("alice", PASSWORD)
    async with app.router.lifespan_context(app), client_for(app) as client:
        await login(client)
        async def chunks():
            yield b"123456"
            yield b"789012"
        assert (await client.post("/api/files/documents/big.txt", content=chunks())).status_code == 413
        assert (await client.post("/api/files/documents/one.txt", content="123456789")).status_code == 200
        assert (await client.post("/api/files/documents/two.txt", content="123456789")).status_code == 422
        assert (await client.delete("/api/files/documents/one.txt")).status_code == 200
        assert (await client.post("/api/files/documents/two.txt", content="123456789")).status_code == 200
        (tmp_path / "maintenance.flag").touch()
        state = (await client.get("/api/state")).json()
        assert (await client.post("/api/tasks", json={"text": "test", "session_id": state["active"]["id"]})).status_code == 503
        assert (await client.get("/healthz")).json()["draining"]


def test_password_hash_and_login_rate_limit(tmp_path):
    accounts = Accounts(tmp_path)
    accounts.create_user("alice", PASSWORD)
    assert PASSWORD.encode() not in accounts.path.read_bytes()
    for _ in range(10):
        with pytest.raises(PermissionError):
            accounts.login("alice", "wrong-but-long-password", "peer")
    with pytest.raises(ValueError, match="too many"):
        accounts.login("alice", PASSWORD, "peer")


def test_atomic_quota_reservation_is_bounded_under_concurrency(tmp_path):
    accounts = Accounts(tmp_path)
    user_id = accounts.create_user("alice", PASSWORD)
    def reserve(_):
        try:
            accounts.reserve(user_id, 10, 5, 50)
            return True
        except ProviderError:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(reserve, range(16))) == 5
    assert accounts.usage(user_id)["charged_tokens"] == 50


@pytest.mark.asyncio
async def test_metering_settles_known_usage_and_keeps_unknown_and_cancelled_reservations(tmp_path):
    accounts = Accounts(tmp_path)
    user_id = accounts.create_user("alice", PASSWORD)
    response = reply()
    response.usage = Usage(input_tokens=7, output_tokens=3, total_tokens=10)
    provider = MeteredProvider(ScriptedProvider([response, reply()]), accounts, user_id, max_calls=10, max_tokens=100_000)
    request = ChatRequest(system_prompt="test", messages=[])
    model = ModelSpec(provider="scripted", id="test")
    await provider.complete(model, request)
    assert accounts.usage(user_id)["charged_tokens"] == 10
    await provider.complete(model, request)
    assert accounts.usage(user_id)["charged_tokens"] > 10
    class Cancelled(ScriptedProvider):
        async def complete(self, model, request):
            raise asyncio.CancelledError
    cancelled = MeteredProvider(Cancelled([]), accounts, user_id, max_calls=10, max_tokens=100_000)
    before = accounts.usage(user_id)["charged_tokens"]
    with pytest.raises(asyncio.CancelledError):
        await cancelled.complete(model, request)
    assert accounts.usage(user_id)["charged_tokens"] > before
    low = MeteredProvider(ScriptedProvider([reply()]), accounts, user_id, max_calls=10, max_tokens=1)
    with pytest.raises(ProviderError, match="quota"):
        await low.complete(model, request)


@pytest.mark.parametrize("name", ["../escape.md", "C:\\secret.txt", "/etc/passwd", ".env", "CON.txt", "bad\nname.txt"])
def test_file_names_cannot_escape_storage(tmp_path, name):
    store = FileStore(tmp_path)
    with pytest.raises(ValueError):
        store.put("documents", name, b"test")


def test_symbolic_link_cannot_expose_other_user_data(tmp_path):
    store = FileStore(tmp_path / "alice")
    secret = tmp_path / "secret.txt"
    secret.write_text("other user")
    try:
        (store.root / "documents" / "link.txt").symlink_to(secret)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symbolic-link privilege unavailable")
        raise
    with pytest.raises(ValueError, match="symbolic"):
        store.path("documents", "link.txt")


@pytest.mark.asyncio
async def test_sse_stops_when_login_is_revoked(tmp_path):
    app = create_server_app(ServerConfig(tmp_path, public_url=ORIGIN))
    user_id = app.state.accounts.create_user("alice", PASSWORD)
    async with app.router.lifespan_context(app), client_for(app) as client:
        await login(client)
        await client.get("/api/state")
        token = client.cookies.get(COOKIE)
        service = app.state.services[user_id]
        stream = service.stream(lambda: app.state.accounts.authenticate(token) is not None)
        await anext(stream)
        app.state.accounts.logout(token)
        with pytest.raises(StopAsyncIteration):
            await anext(stream)


@pytest.mark.asyncio
async def test_parallel_task_admission_respects_global_limit(tmp_path):
    class Waiting(ScriptedProvider):
        async def complete(self, model, request):
            await asyncio.Event().wait()

    app = create_server_app(ServerConfig(tmp_path, public_url=ORIGIN, model="test", provider_id="scripted", concurrent_runs=1),
                            provider_factory=lambda _: Waiting([]))
    app.state.accounts.create_user("alice", PASSWORD)
    app.state.accounts.create_user("bob", PASSWORD)
    async with app.router.lifespan_context(app), client_for(app) as alice, client_for(app) as bob:
        await login(alice)
        await login(bob, "bob")
        states = await asyncio.gather(alice.get("/api/state"), bob.get("/api/state"))
        responses = await asyncio.gather(*(
            client.post("/api/tasks", json={"session_id": state.json()["active"]["id"], "text": "wait"})
            for client, state in zip((alice, bob), states)
        ))
        assert sorted(response.status_code for response in responses) == [200, 429]
        assert sum(service.task is not None for service in app.state.services.values()) == 1


@pytest.mark.asyncio
async def test_chunked_json_and_stalled_upload_are_bounded(tmp_path, monkeypatch):
    import coding_agent.web as web
    app = create_server_app(ServerConfig(tmp_path, public_url=ORIGIN))
    app.state.accounts.create_user("alice", PASSWORD)

    async def oversized():
        yield b" " * 20_000
        yield b" " * 20_000

    async def timed_out():
        yield b"part"
        await asyncio.Event().wait()

    async with app.router.lifespan_context(app), client_for(app) as client:
        await login(client)
        assert (await client.post("/api/sessions", content=oversized())).status_code == 413
        original = asyncio.timeout
        monkeypatch.setattr(web.asyncio, "timeout", lambda _: original(0.01))
        assert (await client.post("/api/files/documents/timeout.txt", content=timed_out())).status_code == 408
        assert (await client.get("/api/files/documents/timeout.txt")).status_code == 404


def test_password_reset_during_verification_cannot_create_old_login(tmp_path, monkeypatch):
    import coding_agent.accounts as module
    accounts = Accounts(tmp_path)
    accounts.create_user("alice", PASSWORD)
    original = module.check_password

    def reset(password, stored):
        result = original(password, stored)
        accounts.reset_password("alice", PASSWORD + "new")
        return result

    monkeypatch.setattr(module, "check_password", reset)
    with pytest.raises(PermissionError, match="changed"):
        accounts.login("alice", PASSWORD, "test")
