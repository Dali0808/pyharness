from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import sys
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field, replace
from io import StringIO
from pathlib import Path
from typing import Any, AsyncIterator, Literal, Sequence
from collections.abc import Callable
from urllib.parse import quote
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from agent.events import (
    AgentEvent, ModelRequested, ModelResponded, ModelTextDelta,
    SubtaskFinished, SubtaskStarted, ToolFinished, ToolStarted,
)
from ai.schemas import AssistantMessage, ModelSpec, ToolCallPart, ToolResultMessage, UserMessage
from coding_agent.builtins import resolve_workspace_path
from coding_agent.cli import (
    CliConfig, ProviderFactory, assistant_text, close_provider, create_provider, run_task,
)
from coding_agent.files import ASSISTANT_SYSTEM_PROMPT, FileLimits, FileStore
from coding_agent.session import JsonlSessionStore, validate_session_metadata
from coding_agent.settings import load_settings, parse_interface_args, save_settings


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class TaskInput(Input):
    text: str = Field(min_length=1, max_length=8000)
    session_id: str


class SessionInput(Input):
    pass


class TrustInput(Input):
    trusted: Literal[True]


@dataclass
class WebSession:
    id: str
    path: str
    title: str = "新会话"
    phase: str = "idle"
    messages: list[dict[str, str]] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    live: str = ""
    failure: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    steps: int = 0
    notices: str = ""


def clip(text: str) -> str:
    return text if len(text) <= 32_000 else text[:32_000] + "\n[界面显示已截短，完整历史保存在 JSONL]"


class RunOutput(StringIO):
    def write(self, text: str) -> int:
        result = super().write(text)
        if self.tell() > 64_000:
            tail = self.getvalue()[-32_000:]
            self.seek(0)
            self.truncate()
            super().write(tail)
        return result


class WebService:
    """One workspace and one active run; execution remains in run_task()."""

    def __init__(self, config: CliConfig, provider_factory: ProviderFactory, *, file_limits: FileLimits = FileLimits()) -> None:
        self.files = FileStore(resolve_workspace_path(config.workspace, ".runtime/assistant"), file_limits)
        session_folder = self.files.root / "sessions"
        session_folder.mkdir(exist_ok=True, mode=0o700)
        selected = config.session_path
        if selected is None or selected.parent != session_folder:
            saved = load_settings(self.files.root)
            selected = resolve_workspace_path(self.files.root, saved.session) if saved.session.startswith("sessions/") else session_folder / "default.jsonl"
        self.config = config = replace(config, system_prompt=ASSISTANT_SYSTEM_PROMPT, session_path=selected)
        self.provider_factory = provider_factory
        self.trusted = False
        self.sessions: dict[str, WebSession] = {}
        self.active = self.add_session(str(config.session_path.relative_to(config.workspace)))
        self.task: asyncio.Task[None] | None = None
        self.run_id: str | None = None
        self.revision = 0
        self.changed = asyncio.Event()
        self.clients = 0
        self.disconnect_timer: asyncio.Task[None] | None = None

    def path(self, relative: str) -> Path:
        path = resolve_workspace_path(self.config.workspace, relative)
        if path.parent != self.files.root / "sessions" or path.suffix != ".jsonl":
            raise ValueError("assistant sessions must stay in the managed session directory")
        if path.is_dir():
            raise ValueError("session path must be a file")
        return path

    def add_session(self, relative: str) -> WebSession:
        path = self.path(relative)
        relative = path.relative_to(self.config.workspace).as_posix()
        key = hashlib.sha256(relative.encode()).hexdigest()[:24]
        if key not in self.sessions:
            self.sessions[key] = WebSession(id=key, path=relative)
        return self.sessions[key]

    def notify(self) -> None:
        self.revision += 1
        self.changed.set()

    def require_idle(self) -> None:
        if self.task is not None:
            raise HTTPException(409, "请先完成或停止当前任务")

    def trim_view(self) -> None:
        for values, fields, count in ((self.active.messages, ("text",), 200), (self.active.tools, ("arguments", "output", "detail"), 100)):
            while len(values) > count or (len(values) > 1 and sum(len(item.get(name, "")) for item in values for name in fields) > 256_000):
                del values[0]

    def require_trust(self) -> None:
        if not self.trusted:
            raise HTTPException(403, "请先信任当前工作区")

    def restore(self, session: WebSession, *, recover: bool = False) -> None:
        path = self.path(session.path)
        if not path.exists():
            return
        if path.stat().st_size > 32_000_000:
            raise ValueError("session exceeds the 32 MB web loading limit; use a new session")
        store = JsonlSessionStore(path)
        if Path(store.load_metadata().workspace).resolve() != self.config.workspace:
            raise ValueError("session belongs to a different workspace")
        snapshot = store.recover() if recover else store.load()
        session.messages = []
        session.tools = []
        for message in snapshot.messages:
            if isinstance(message, UserMessage):
                session.messages.append({"role": "user", "text": clip(message.content)})
                if session.title == "新会话":
                    session.title = message.content[:40]
            elif isinstance(message, AssistantMessage):
                text = assistant_text(message)
                if text:
                    session.messages.append({"role": "assistant", "text": clip(text)})
                for part in message.content:
                    if isinstance(part, ToolCallPart):
                        session.tools.append({"id": part.id, "name": part.name, "arguments": clip(part.arguments_json), "status": "interrupted", "output": "结果未知，不会自动重放"})
            elif isinstance(message, ToolResultMessage):
                for tool in reversed(session.tools):
                    if tool["id"] == message.tool_call_id:
                        tool.update(status="error" if message.is_error else "completed", output=clip(message.content))
                        break
        session.messages = session.messages[-200:]
        session.tools = session.tools[-100:]
        if snapshot.runs:
            status = snapshot.runs[-1].status
            session.phase = {"completed": "completed", "failed": "failed", "interrupted": "cancelled", "running": "cancelled"}[status]
            session.failure = snapshot.runs[-1].failure_code

    def trust(self) -> None:
        self.require_idle()
        folder = self.files.root / "sessions"
        self.trusted = True
        if folder.is_dir():
            for path in sorted(folder.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:100]:
                if not path.is_symlink() and not path.name.endswith(".subtasks.jsonl"):
                    self.add_session(path.relative_to(self.config.workspace).as_posix())
        for session in self.sessions.values():
            try:
                self.restore(session, recover=True)
            except (OSError, ValueError) as exc:
                session.failure = str(exc)
                session.phase = "failed"
            if session is not self.active:
                session.messages = []
                session.tools = []
        self.notify()

    def snapshot(self) -> dict[str, Any]:
        self.trim_view()
        return {
            "revision": self.revision, "trusted": self.trusted,
            "workspace": "资料空间", "settings": {"model_id": self.config.model_id}, "mode": "assistant",
            "api_key_set": bool(os.environ.get(self.config.api_key_env)),
            "sessions": [{"id": s.id, "title": s.title, "path": s.path} for s in self.sessions.values()],
            "active": {**vars(self.active), "run_id": self.run_id},
            "files": self.files.listing(),
            "busy": self.task is not None,
        }

    def select(self, session: WebSession) -> None:
        self.restore(session, recover=True)
        updated = replace(self.config, session_path=self.path(session.path))
        save_settings(replace(updated, workspace=self.files.root))
        self.config = updated
        if self.active is not session:
            self.active.messages = []
            self.active.tools = []
        self.active = session
        self.notify()

    def start(self, body: TaskInput) -> None:
        self.require_trust()
        self.require_idle()
        if body.session_id != self.active.id:
            raise HTTPException(409, "当前会话已切换，请刷新后重试")
        if not body.text.strip() or not self.config.model_id.strip():
            raise HTTPException(422, "任务和模型名称不能为空，请先配置模型")
        if sum(p.stat().st_size for p in (self.files.root / "sessions").glob("*.jsonl") if not p.is_symlink()) > 32_000_000:
            raise HTTPException(413, "会话历史已超过 32 MB，请先删除旧会话")
        path = self.path(self.active.path)
        if self.files.listing()["bytes"] > self.files.limits.max_storage_bytes:
            raise HTTPException(413, "资料空间超出存储限额，请先删除文件")
        if path.exists():
            if path.stat().st_size > 32_000_000:
                raise ValueError("session exceeds the 32 MB web loading limit; use a new session")
            validate_session_metadata(
                JsonlSessionStore(path).load_metadata(), workspace=self.config.workspace,
                model=ModelSpec(provider=self.config.provider_id, id=self.config.model_id, context_window=self.config.context_window),
                system_prompt=self.config.system_prompt, multi_agent=self.config.multi_agent,
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        self.active.messages.append({"role": "user", "text": body.text.strip()})
        self.active.messages = self.active.messages[-200:]
        self.active.title = self.active.title if self.active.title != "新会话" else body.text.strip()[:40]
        self.active.phase = "running"
        self.active.failure = None
        self.active.live = ""
        self.active.notices = ""
        self.active.usage = {}
        self.active.steps = 0
        self.run_id = uuid4().hex
        config = replace(self.config, task=body.text.strip(), session_path=path)
        self.task = asyncio.create_task(self.run(config))
        self.arm_disconnect()
        self.notify()

    def on_event(self, event: AgentEvent) -> None:
        session = self.active
        if isinstance(event, ModelRequested):
            session.live = ""
            session.steps = event.step
        elif isinstance(event, ModelTextDelta):
            session.live = clip(session.live + event.text)
        elif isinstance(event, ModelResponded):
            text = assistant_text(event.response.message)
            if text:
                session.messages.append({"role": "assistant", "text": clip(text)})
                session.messages = session.messages[-200:]
            session.live = ""
        elif isinstance(event, ToolStarted):
            session.tools.append({"id": event.call.id, "name": event.call.name, "arguments": clip(event.call.arguments_json), "status": "running", "output": ""})
            session.tools = session.tools[-100:]
        elif isinstance(event, ToolFinished):
            for tool in reversed(session.tools):
                if tool["id"] == event.result.tool_call_id:
                    tool.update(status="error" if event.result.is_error else "completed", output=clip(event.result.content))
                    break
        elif isinstance(event, (SubtaskStarted, SubtaskFinished)):
            session.notices = clip(session.notices + (f"\n只读子任务：{event.task}" if isinstance(event, SubtaskStarted) else f"\n子任务结束：{event.failure_code or 'completed'}，{event.steps} 步"))
        self.notify()

    async def run(self, config: CliConfig) -> None:
        provider = None
        output = RunOutput()
        try:
            provider = self.provider_factory(config)
            result = await run_task(
                config, provider, output, on_event=self.on_event,
                tools=self.files.tools(), read_root=self.files.root / "documents", memory_from_env=False,
            )
            failure = result.run_result.failure
            self.active.phase = "completed" if result.exit_code == 0 else "cancelled" if failure and failure.code == "cancelled" else "failed"
            self.active.failure = f"{failure.code}: {failure.message}" if failure else None
            self.active.usage = result.run_result.usage.model_dump()
            self.active.steps = result.run_result.steps
        except asyncio.CancelledError:
            self.active.phase = "cancelled"
            self.active.failure = "任务已停止"
        except Exception as exc:
            self.active.phase = "failed"
            self.active.failure = f"{type(exc).__name__}: {exc}"
        finally:
            for tool in self.active.tools:
                if tool["status"] == "running":
                    tool.update(status="interrupted", output="操作被中断；检查工作区后再决定是否重试，不会自动重放。")
            # A partial stream is visible but never represented as durable history.
            if self.active.live:
                self.active.messages.append({"role": "partial", "text": self.active.live})
                self.active.live = ""
            self.active.notices = clip(self.active.notices + "\n" + "\n".join(line for line in output.getvalue().splitlines() if line.startswith(("memory ", "context compacted:"))))
            try:
                if provider is not None:
                    await close_provider(provider)
            except Exception:
                self.active.notices += "\n模型连接清理失败，连接将在服务退出时关闭。"
            finally:
                self.task = None
                self.run_id = None
                self.notify()

    async def stop(self) -> None:
        task = self.task
        if task is None:
            return
        self.active.phase = "stopping"
        self.notify()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        if self.task is task:
            self.task = None
            self.run_id = None
            self.active.phase = "cancelled"
            self.notify()

    def arm_disconnect(self) -> None:
        if self.disconnect_timer:
            self.disconnect_timer.cancel()
        if self.clients == 0:
            self.disconnect_timer = asyncio.create_task(self.stop_disconnected())

    async def stop_disconnected(self) -> None:
        await asyncio.sleep(20)
        if self.clients == 0:
            self.disconnect_timer = None
            await self.stop()

    async def stream(self, authorized: Callable[[], bool] | None = None) -> AsyncIterator[str]:
        self.clients += 1
        if self.disconnect_timer:
            self.disconnect_timer.cancel()
        revision = -1
        try:
            while True:
                if authorized is not None and not authorized():
                    return
                self.changed.clear()
                if revision != self.revision:
                    revision = self.revision
                    yield "data: " + json.dumps(self.snapshot(), ensure_ascii=False) + "\n\n"
                    # shortcut: bounded snapshots at 20 Hz; switch to deltas if long UI histories lag.
                    await asyncio.sleep(0.05)
                else:
                    try:
                        await asyncio.wait_for(self.changed.wait(), timeout=10)
                    except TimeoutError:
                        yield ": heartbeat\n\n"
        finally:
            self.clients -= 1
            self.arm_disconnect()

    async def close(self) -> None:
        if self.disconnect_timer:
            self.disconnect_timer.cancel()
        await self.stop()


def create_web_app(
    config: CliConfig, *, token: str, port: int = 8000,
    provider_factory: ProviderFactory = create_provider, static_dir: Path | None = None,
) -> FastAPI:
    if not token:
        raise ValueError("a nonempty connection token is required")
    service = WebService(config, provider_factory)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await service.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.service = service
    allowed_host = f"127.0.0.1:{port}"
    origin = f"http://{allowed_host}"

    @app.middleware("http")
    async def local_only(request: Request, call_next: Any) -> Any:
        if request.headers.get("host") != allowed_host or (request.headers.get("origin") not in (None, origin)):
            return JSONResponse({"detail": "只允许本机同源访问"}, status_code=403)
        if request.url.path.startswith("/api/"):
            if not secrets.compare_digest(request.headers.get("authorization", "").encode(), f"Bearer {token}".encode()):
                return JSONResponse({"detail": "请使用启动终端中带连接令牌的地址"}, status_code=401)
            limit = 1_000_000 if request.url.path.startswith("/api/files/documents/") else 32_768
            if request.headers.get("content-length", "0").isdigit() and int(request.headers.get("content-length", "0")) > limit:
                return JSONResponse({"detail": "请求内容过大"}, status_code=413)
            if request.method not in {"GET", "HEAD"}:
                try:
                    request._body = await bounded_body(request, limit)
                except HTTPException as exc:
                    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        response = await call_next(request)
        secure_headers(response)
        return response

    @app.get("/api/auth/mode")
    async def auth_mode():
        return {"mode": "token"}
    add_workspace_routes(app, lambda _: service)
    mount_frontend(app, static_dir)
    return app


def secure_headers(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"


async def bounded_body(request: Request, limit: int) -> bytes:
    data = bytearray()
    try:
        async with asyncio.timeout(30):
            async for chunk in request.stream():
                if len(data) + len(chunk) > limit:
                    raise HTTPException(413, "请求内容过大")
                data.extend(chunk)
    except TimeoutError:
        raise HTTPException(408, "上传超时，请重试") from None
    return bytes(data)


def add_workspace_routes(app: FastAPI, service_for: Callable[[Request], WebService]) -> None:
    @app.exception_handler(ValueError)
    async def invalid(request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(OSError)
    async def storage_error(request: Request, exc: OSError) -> JSONResponse:
        status = 409 if isinstance(exc, FileExistsError) else 404 if isinstance(exc, FileNotFoundError) else 500
        return JSONResponse({"detail": f"文件操作失败：{type(exc).__name__}"}, status_code=status)

    @app.get("/api/state")
    async def state(request: Request) -> dict[str, Any]:
        return service_for(request).snapshot()

    @app.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        return StreamingResponse(service_for(request).stream(getattr(request.state, "authorized", None)), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @app.post("/api/trust")
    async def trust(request: Request, body: TrustInput) -> dict[str, Any]:
        service = service_for(request)
        service.trust()
        return service.snapshot()

    @app.post("/api/sessions")
    async def new_session(request: Request, body: SessionInput) -> dict[str, Any]:
        service = service_for(request)
        service.require_trust()
        service.require_idle()
        if len(service.sessions) >= 100:
            raise HTTPException(413, "最多保留 100 个会话，请先删除旧会话")
        relative = (service.files.root / "sessions" / f"{uuid4().hex}.jsonl").relative_to(service.config.workspace).as_posix()
        session = service.add_session(relative)
        service.select(session)
        return service.snapshot()

    @app.post("/api/sessions/{session_id}/select")
    async def select_session(request: Request, session_id: str) -> dict[str, Any]:
        service = service_for(request)
        service.require_trust()
        service.require_idle()
        if session_id not in service.sessions:
            raise HTTPException(404, "会话不存在")
        session = service.sessions[session_id]
        service.select(session)
        return service.snapshot()

    @app.post("/api/tasks")
    async def tasks(request: Request, body: TaskInput) -> dict[str, Any]:
        service = service_for(request)
        service.start(body)
        return service.snapshot()

    @app.post("/api/stop")
    async def stop(request: Request) -> dict[str, Any]:
        service = service_for(request)
        await service.stop()
        return service.snapshot()

    @app.post("/api/files/documents/{name}")
    async def upload(request: Request, name: str) -> dict[str, Any]:
        service = service_for(request)
        service.require_trust()
        service.require_idle()
        data = await bounded_body(request, service.files.limits.max_file_bytes)
        service.require_idle()
        service.files.put("documents", name, data)
        service.notify()
        return service.snapshot()

    @app.get("/api/files/{kind}/{name}")
    async def download(request: Request, kind: Literal["documents", "artifacts"], name: str) -> Response:
        service = service_for(request)
        service.require_trust()
        path = service.files.path(kind, name)
        if not path.is_file():
            raise HTTPException(404, "文件不存在")
        if path.stat().st_size > service.files.limits.max_file_bytes:
            raise HTTPException(413, "文件超过大小限制")
        return Response(path.read_bytes(), media_type="application/octet-stream", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name, safe='')}"})

    @app.delete("/api/files/{kind}/{name}")
    async def delete_file(request: Request, kind: Literal["documents", "artifacts"], name: str) -> dict[str, Any]:
        service = service_for(request)
        service.require_trust()
        service.require_idle()
        service.files.path(kind, name).unlink()
        service.notify()
        return service.snapshot()

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(request: Request, session_id: str) -> dict[str, Any]:
        service = service_for(request)
        service.require_trust()
        service.require_idle()
        session = service.sessions.get(session_id)
        if session is None:
            raise HTTPException(404, "会话不存在")
        path = service.path(session.path)
        path.unlink(missing_ok=True)
        path.with_name(path.name + ".subtasks.jsonl").unlink(missing_ok=True)
        del service.sessions[session_id]
        if service.active is session:
            if not service.sessions:
                relative = (path.parent / f"{uuid4().hex}.jsonl").relative_to(service.config.workspace).as_posix()
                service.add_session(relative)
            service.select(next(iter(service.sessions.values())))
        service.notify()
        return service.snapshot()

    @app.api_route("/api/{unknown:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
    async def unknown_api(unknown: str):
        raise HTTPException(404, "接口不存在")


def mount_frontend(app: FastAPI, static_dir: Path | None = None) -> None:
    assets = static_dir or Path(__file__).resolve().parent.parent / "frontend" / "dist"
    if assets.is_dir():
        app.mount("/", StaticFiles(directory=assets, html=True), name="frontend")


def main(argv: Sequence[str] | None = None) -> None:
    import uvicorn

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--port", type=int, default=8000)
    args, remaining = parser.parse_known_args(argv if argv is not None else sys.argv[1:])
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    config = parse_interface_args(remaining, description="Run Lario Web on 127.0.0.1. Additional option: --port PORT (default: 8000).")
    assets = Path(__file__).resolve().parent.parent / "frontend" / "dist"
    if not (assets / "index.html").is_file():
        raise SystemExit("Build the web UI first: cd frontend && npm ci && npm run build")
    token = secrets.token_urlsafe(32)
    print(f"Lario Web workspace: {config.workspace}\nOpen http://127.0.0.1:{args.port}/#token={token}", flush=True)
    uvicorn.run(create_web_app(config, token=token, port=args.port), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
