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
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
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
from coding_agent.command import format_command
from coding_agent.session import JsonlSessionStore, validate_session_metadata
from coding_agent.settings import WorkspaceSettings, parse_interface_args, save_settings


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class TaskInput(Input):
    text: str = Field(min_length=1, max_length=8000)
    session_id: str


class SessionInput(Input):
    path: str | None = Field(default=None, min_length=1, max_length=500)


class TrustInput(Input):
    trusted: Literal[True]


class DecisionInput(Input):
    approved: bool
    run_id: str


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

    def __init__(self, config: CliConfig, provider_factory: ProviderFactory) -> None:
        self.config = config
        self.provider_factory = provider_factory
        self.trusted = False
        self.sessions: dict[str, WebSession] = {}
        self.active = self.add_session(str(config.session_path.relative_to(config.workspace)))
        self.task: asyncio.Task[None] | None = None
        self.run_id: str | None = None
        self.approval: dict[str, Any] | None = None
        self.decision: asyncio.Future[bool] | None = None
        self.current_call: ToolCallPart | None = None
        self.revision = 0
        self.changed = asyncio.Event()
        self.clients = 0
        self.disconnect_timer: asyncio.Task[None] | None = None

    def path(self, relative: str) -> Path:
        path = resolve_workspace_path(self.config.workspace, relative)
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
        folder = resolve_workspace_path(self.config.workspace, ".runtime/web/sessions")
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
        self.notify()

    def snapshot(self) -> dict[str, Any]:
        self.trim_view()
        settings = {name: getattr(self.config, name) for name in WorkspaceSettings.model_fields if name != "session"}
        settings["session"] = self.active.path
        return {
            "revision": self.revision, "trusted": self.trusted,
            "workspace": str(self.config.workspace), "settings": settings,
            "api_key_set": bool(os.environ.get(self.config.api_key_env)),
            "sessions": [{"id": s.id, "title": s.title, "path": s.path} for s in self.sessions.values()],
            "active": {**vars(self.active), "run_id": self.run_id, "approval": self.approval},
            "busy": self.task is not None,
        }

    def configure(self, settings: WorkspaceSettings) -> None:
        self.require_trust()
        self.require_idle()
        path = self.path(settings.session)
        updated = replace(self.config, **settings.model_dump(exclude={"session"}), session_path=path)
        session = self.add_session(settings.session)
        self.restore(session, recover=True)
        save_settings(updated)
        self.config = updated
        self.active = session
        self.notify()

    def select(self, session: WebSession) -> None:
        self.restore(session, recover=True)
        updated = replace(self.config, session_path=self.path(session.path))
        save_settings(updated)
        self.config = updated
        self.active = session
        self.notify()

    def start(self, body: TaskInput) -> None:
        self.require_trust()
        self.require_idle()
        if body.session_id != self.active.id:
            raise HTTPException(409, "当前会话已切换，请刷新后重试")
        if not body.text.strip() or not self.config.model_id.strip():
            raise HTTPException(422, "任务和模型名称不能为空，请先配置模型")
        path = self.path(self.active.path)
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
            self.current_call = event.call
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

    async def ask(self, kind: str, detail: str) -> bool:
        self.decision = asyncio.get_running_loop().create_future()
        self.approval = {"id": uuid4().hex, "run_id": self.run_id, "kind": kind, "detail": detail}
        if self.active.tools:
            self.active.tools[-1]["detail"] = detail
            if kind == "edit" and self.current_call is not None:
                self.approval["path"] = json.loads(self.current_call.arguments_json).get("path", "")
        self.active.phase = "awaiting_edit" if kind == "edit" else "awaiting_command"
        self.notify()
        try:
            return await self.decision
        finally:
            self.approval = None
            self.current_call = None
            self.decision = None
            if self.active.phase != "stopping":
                self.active.phase = "running"
            self.notify()

    def decide(self, approval_id: str, body: DecisionInput) -> None:
        if not self.approval or self.approval["id"] != approval_id or body.run_id != self.run_id or self.decision is None or self.decision.done():
            raise HTTPException(409, "此审批已失效，请使用当前审批")
        self.decision.set_result(body.approved)
        self.approval = None
        self.notify()

    async def run(self, config: CliConfig) -> None:
        provider = None
        output = RunOutput()
        try:
            provider = self.provider_factory(config)
            result = await run_task(
                config, provider, output, on_event=self.on_event,
                approve=lambda diff: self.ask("edit", diff),
                approve_command=lambda argv, cwd: self.ask("command", format_command(argv, cwd)),
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
            self.approval = None
            self.current_call = None
            if self.decision is not None and not self.decision.done():
                self.decision.cancel()
            self.decision = None
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
        self.approval = None
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

    async def stream(self) -> AsyncIterator[str]:
        self.clients += 1
        if self.disconnect_timer:
            self.disconnect_timer.cancel()
        revision = -1
        try:
            while True:
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
            if request.headers.get("content-length", "0").isdigit() and int(request.headers.get("content-length", "0")) > 32_768:
                return JSONResponse({"detail": "请求内容过大"}, status_code=413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.exception_handler(ValueError)
    async def invalid(request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(OSError)
    async def storage_error(request: Request, exc: OSError) -> JSONResponse:
        return JSONResponse({"detail": f"本地存储不可用：{type(exc).__name__}"}, status_code=500)

    @app.get("/api/state")
    async def state() -> dict[str, Any]:
        return service.snapshot()

    @app.get("/api/events")
    async def events() -> StreamingResponse:
        return StreamingResponse(service.stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @app.post("/api/trust")
    async def trust(body: TrustInput) -> dict[str, Any]:
        service.trust()
        return service.snapshot()

    @app.post("/api/settings")
    async def settings(body: WorkspaceSettings) -> dict[str, Any]:
        service.configure(body)
        return service.snapshot()

    @app.post("/api/sessions")
    async def new_session(body: SessionInput) -> dict[str, Any]:
        service.require_trust()
        service.require_idle()
        session = service.add_session(body.path or f".runtime/web/sessions/{uuid4().hex}.jsonl")
        service.select(session)
        return service.snapshot()

    @app.post("/api/sessions/{session_id}/select")
    async def select_session(session_id: str) -> dict[str, Any]:
        service.require_trust()
        service.require_idle()
        if session_id not in service.sessions:
            raise HTTPException(404, "会话不存在")
        session = service.sessions[session_id]
        service.select(session)
        return service.snapshot()

    @app.post("/api/tasks")
    async def tasks(body: TaskInput) -> dict[str, Any]:
        service.start(body)
        return service.snapshot()

    @app.post("/api/approvals/{approval_id}")
    async def decide(approval_id: str, body: DecisionInput) -> dict[str, Any]:
        service.decide(approval_id, body)
        return service.snapshot()

    @app.post("/api/stop")
    async def stop() -> dict[str, Any]:
        await service.stop()
        return service.snapshot()

    assets = static_dir or Path(__file__).resolve().parent.parent / "frontend" / "dist"
    if assets.is_dir():
        app.mount("/", StaticFiles(directory=assets, html=True), name="frontend")
    return app


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
