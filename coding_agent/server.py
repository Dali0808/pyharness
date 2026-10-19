"""Single-process, administrator-provisioned Web assistant service."""
from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import re
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from coding_agent.accounts import Accounts, MeteredProvider
from coding_agent.cli import CliConfig, create_provider
from coding_agent.files import ASSISTANT_SYSTEM_PROMPT, FileLimits
from coding_agent.web import WebService, add_workspace_routes, bounded_body, mount_frontend, secure_headers


COOKIE = "lario_session"


@dataclass(frozen=True)
class ServerConfig:
    data_dir: Path
    public_url: str = "http://127.0.0.1:8000"
    model: str = ""
    base_url: str = "https://api.deepseek.com/v1"
    api_key_env: str = "DEEPSEEK_API_KEY"
    provider_id: str = "openai"
    context_window: int = 64000
    max_steps: int = 20
    daily_requests: int = 200
    daily_tokens: int = 1_000_000
    concurrent_runs: int = 2
    file_limits: FileLimits = field(default_factory=FileLimits)

    def __post_init__(self):
        url = urlsplit(self.public_url)
        if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.path not in {"", "/"}):
            raise ValueError("public URL must be an http(s) origin without path or credentials")
        if url.scheme != "https" and url.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("public deployment requires HTTPS")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.api_key_env):
            raise ValueError("invalid API key environment variable name")
        if min(self.context_window, self.max_steps, self.daily_requests, self.daily_tokens, self.concurrent_runs) <= 0:
            raise ValueError("server budgets must be positive")
        if not 1 <= self.max_steps <= 100:
            raise ValueError("max steps must be between 1 and 100")
        api = urlsplit(self.base_url)
        if api.scheme not in {"https", "http"} or not api.hostname or api.username or api.password or api.query or api.fragment:
            raise ValueError("invalid model API base URL")

    @classmethod
    def from_env(cls, data_dir: Path | None = None):
        return cls(
            data_dir=data_dir or Path(os.environ.get("LARIO_DATA_DIR", ".runtime/server")),
            public_url=os.environ.get("LARIO_PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/"),
            model=os.environ.get("LARIO_MODEL", ""),
            base_url=os.environ.get("LARIO_MODEL_BASE_URL", "https://api.deepseek.com/v1"),
            api_key_env=os.environ.get("LARIO_API_KEY_ENV", "DEEPSEEK_API_KEY"),
            provider_id=os.environ.get("LARIO_PROVIDER_ID", "openai"),
            context_window=int(os.environ.get("LARIO_CONTEXT_WINDOW", "64000")),
            max_steps=int(os.environ.get("LARIO_MAX_STEPS", "20")),
            daily_requests=int(os.environ.get("LARIO_DAILY_REQUESTS", "200")),
            daily_tokens=int(os.environ.get("LARIO_DAILY_TOKENS", "1000000")),
            concurrent_runs=int(os.environ.get("LARIO_CONCURRENT_RUNS", "2")),
            file_limits=FileLimits(
                max_file_bytes=int(os.environ.get("LARIO_MAX_FILE_BYTES", "1000000")),
                max_storage_bytes=int(os.environ.get("LARIO_STORAGE_BYTES", "50000000")),
                max_files=int(os.environ.get("LARIO_MAX_FILES", "200")),
            ),
        )


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class UserService(WebService):
    def __init__(self, config: ServerConfig, user: dict, accounts: Accounts, factory):
        self.user = user
        self.accounts = accounts
        self.server_config = config
        root = config.data_dir / "users" / user["id"]
        if root.is_symlink() or root.parent.is_symlink():
            raise ValueError("user directories must not be symbolic links")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        run_config = CliConfig(
            task="", workspace=root.resolve(), provider_id=config.provider_id, model_id=config.model,
            base_url=config.base_url, api_key_env=config.api_key_env, system_prompt=ASSISTANT_SYSTEM_PROMPT,
            max_steps=config.max_steps, context_window=config.context_window,
        )
        super().__init__(run_config, lambda c: MeteredProvider(
            factory(c), accounts, user["id"], max_calls=config.daily_requests, max_tokens=config.daily_tokens,
        ), file_limits=config.file_limits)
        self.trust()

    def start(self, body):
        if (self.server_config.data_dir / "maintenance.flag").exists():
            raise HTTPException(503, "服务维护中，暂不接收新任务")
        super().start(body)

    def snapshot(self):
        state = super().snapshot()
        state["account"] = {"username": self.user["username"], "usage": self.accounts.usage(self.user["id"]),
                            "limits": {"daily_requests": self.server_config.daily_requests, "daily_tokens": self.server_config.daily_tokens}}
        return state


def create_server_app(config: ServerConfig, *, provider_factory=create_provider, static_dir: Path | None = None) -> FastAPI:
    config.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    accounts = Accounts(config.data_dir)
    services: dict[str, UserService] = {}
    login_slots = asyncio.Semaphore(4)
    admission_lock = asyncio.Lock()
    origin = config.public_url.rstrip("/")
    host = urlsplit(origin).netloc

    @asynccontextmanager
    async def lifespan(app):
        yield
        await asyncio.gather(*(service.close() for service in services.values()))

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.accounts = accounts
    app.state.services = services

    def service_for(request: Request) -> UserService:
        user = request.state.user
        if user["id"] not in services:
            if len(services) >= 8:
                idle = next((key for key, service in services.items() if service.task is None and service.clients == 0), None)
                if idle is None:
                    raise HTTPException(503, "服务繁忙，请稍后重试")
                removed = services.pop(idle)
                if removed.disconnect_timer:
                    removed.disconnect_timer.cancel()
            services[user["id"]] = UserService(config, user, accounts, provider_factory)
        return services[user["id"]]

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        if request.headers.get("host") != host or request.headers.get("origin") not in {None, origin}:
            return JSONResponse({"detail": "请求来源不允许"}, status_code=403)
        path = request.url.path
        if path.startswith("/api/"):
            if request.method not in {"GET", "HEAD"} and request.headers.get("origin") != origin:
                return JSONResponse({"detail": "写入请求必须来自当前站点"}, status_code=403)
            if path not in {"/api/auth/login", "/api/auth/mode"}:
                user = accounts.authenticate(request.cookies.get(COOKIE, ""))
                if user is None:
                    return JSONResponse({"detail": "请登录"}, status_code=401)
                request.state.user = user
                request.state.authorized = lambda: accounts.authenticate(request.cookies.get(COOKIE, "")) is not None
            if request.method not in {"GET", "HEAD"}:
                limit = config.file_limits.max_file_bytes if path.startswith("/api/files/documents/") else 32_768
                try:
                    request._body = await bounded_body(request, limit)
                except HTTPException as exc:
                    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
            if path == "/api/tasks" and request.method == "POST":
                async with admission_lock:
                    if (config.data_dir / "maintenance.flag").exists():
                        return JSONResponse({"detail": "服务维护中，暂不接收新任务"}, status_code=503)
                    if sum(service.task is not None for service in services.values()) >= config.concurrent_runs:
                        return JSONResponse({"detail": "服务忙碌，请稍后重试"}, status_code=429)
                    response = await call_next(request)
                    secure_headers(response)
                    return response
        response = await call_next(request)
        secure_headers(response)
        return response

    @app.get("/healthz")
    async def health():
        return {"status": "ok", "version": "0.1.0", "release": os.environ.get("LARIO_RELEASE", "development"),
                "draining": (config.data_dir / "maintenance.flag").exists(),
                "active_runs": sum(service.task is not None for service in services.values()),
                "frontend": (static_dir or Path(__file__).resolve().parent.parent / "frontend" / "dist").joinpath("index.html").is_file()}

    @app.get("/api/auth/mode")
    async def auth_mode():
        return {"mode": "login"}

    @app.post("/api/auth/login")
    async def login(request: Request, body: LoginInput):
        try:
            async with login_slots:
                token = await asyncio.to_thread(accounts.login, body.username, body.password, request.client.host if request.client else "unknown")
        except PermissionError:
            return JSONResponse({"detail": "账号或密码错误"}, status_code=401)
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=429)
        response = JSONResponse({"ok": True})
        response.set_cookie(COOKIE, token, max_age=43200, httponly=True, secure=origin.startswith("https:"), samesite="strict", path="/")
        return response

    @app.post("/api/auth/logout")
    async def logout(request: Request):
        accounts.logout(request.cookies.get(COOKIE, ""))
        service = services.get(request.state.user["id"])
        if service:
            await service.stop()
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/")
        return response

    add_workspace_routes(app, service_for)
    mount_frontend(app, static_dir)
    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description="Lario Web assistant server and account administration")
    parser.add_argument("--data-dir", type=Path, default=None)
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    for command in ("create-user", "disable-user", "reset-password"):
        commands.add_parser(command).add_argument("username")
    args = parser.parse_args(argv)
    try:
        config = ServerConfig.from_env(args.data_dir)
        if args.command == "serve":
            import uvicorn
            if not config.model.strip():
                parser.error("set LARIO_MODEL before starting the server")
            if not os.environ.get(config.api_key_env):
                parser.error(f"set {config.api_key_env} before starting the server")
            assets = Path(__file__).resolve().parent.parent / "frontend" / "dist"
            if not (assets / "index.html").is_file():
                parser.error("frontend build missing; run npm ci && npm run build in frontend")
            uvicorn.run(create_server_app(config), host=args.host, port=args.port, access_log=False, proxy_headers=False,
                        limit_concurrency=128, timeout_keep_alive=5, timeout_graceful_shutdown=30)
        else:
            accounts = Accounts(config.data_dir)
            if args.command == "disable-user":
                accounts.disable(args.username)
            else:
                password = getpass.getpass("Password (12+ characters): ")
                if password != getpass.getpass("Confirm password: "):
                    parser.error("passwords do not match")
                if args.command == "create-user":
                    accounts.create_user(args.username, password)
                else:
                    accounts.reset_password(args.username, password)
            print(f"{args.command}: {args.username}")
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
