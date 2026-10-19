"""SQLite accounts, revocable browser sessions and atomic daily model budgets."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ai.provider import ProviderError


def password_hash(password: str, salt: bytes | None = None) -> str:
    if not 12 <= len(password) <= 1024:
        raise ValueError("password must contain 12 to 1024 characters")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1).hex()
    return f"scrypt${salt.hex()}${digest}"


def check_password(password: str, stored: str) -> bool:
    if not 12 <= len(password) <= 1024:
        return False
    _, salt, _ = stored.split("$")
    return hmac.compare_digest(password_hash(password, bytes.fromhex(salt)), stored)


class Accounts:
    def __init__(self, root: Path) -> None:
        if root.is_symlink():
            raise ValueError("data directory must not be a symbolic link")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = root / "accounts.sqlite3"
        if self.path.is_symlink():
            raise ValueError("database must not be a symbolic link")
        self.dummy_hash = password_hash("unused-password-for-timing", b"\0" * 16)
        with self.connect() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] not in {0, 1}:
                raise ValueError("unsupported account database version; restore the matching application version")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
                    password TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS logins (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    expires INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    bucket TEXT PRIMARY KEY, started INTEGER NOT NULL, count INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS usage (
                    user_id TEXT NOT NULL REFERENCES users(id), day TEXT NOT NULL,
                    calls INTEGER NOT NULL, charged_tokens INTEGER NOT NULL, actual_tokens INTEGER NOT NULL,
                    PRIMARY KEY(user_id, day)
                );
                PRAGMA user_version = 1;
            """)
        if os.name != "nt":
            self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def create_user(self, username: str, password: str) -> str:
        username = username.lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,63}", username):
            raise ValueError("username must be 3-64 ASCII letters, digits, dots, underscores or hyphens")
        hashed = password_hash(password)
        user_id = uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO users(id, username, password) VALUES (?, ?, ?)", (user_id, username, hashed))
        return user_id

    def login(self, username: str, password: str, peer: str, *, lifetime: int = 43200) -> str:
        now = int(time.time())
        username = username.lower()
        buckets = (("peer:" + peer, 100), ("user:" + username, 10))
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM attempts WHERE started < ?", (now - 600,))
            for key, limit in buckets:
                key = hashlib.sha256(key.encode()).hexdigest()
                row = db.execute("SELECT count FROM attempts WHERE bucket = ?", (key,)).fetchone()
                if row and row["count"] >= limit:
                    raise ValueError("too many login attempts; retry after 10 minutes")
            for key, _ in buckets:
                key = hashlib.sha256(key.encode()).hexdigest()
                db.execute("INSERT INTO attempts VALUES (?, ?, 1) ON CONFLICT(bucket) DO UPDATE SET count=count+1", (key, now))
        with self.connect() as db:
            user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        valid = check_password(password, user["password"] if user else self.dummy_hash)
        if not valid or user is None or user["disabled"]:
            raise PermissionError("invalid username or password")
        token = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT password, disabled FROM users WHERE id=?", (user["id"],)).fetchone()
            if current is None or current["disabled"] or current["password"] != user["password"]:
                raise PermissionError("account changed during login; retry")
            db.execute("DELETE FROM logins WHERE expires <= ?", (now,))
            db.execute("DELETE FROM logins WHERE token_hash IN (SELECT token_hash FROM logins WHERE user_id=? ORDER BY expires DESC LIMIT -1 OFFSET 9)", (user["id"],))
            db.execute("INSERT INTO logins VALUES (?, ?, ?)", (hashlib.sha256(token.encode()).hexdigest(), user["id"], now + lifetime))
        return token

    def authenticate(self, token: str) -> dict | None:
        if not token or len(token) > 200:
            return None
        with self.connect() as db:
            row = db.execute("SELECT users.id, users.username FROM logins JOIN users ON users.id=logins.user_id WHERE token_hash=? AND expires>? AND disabled=0", (hashlib.sha256(token.encode()).hexdigest(), int(time.time()))).fetchone()
        return dict(row) if row else None

    def logout(self, token: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM logins WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))

    def disable(self, username: str) -> None:
        with self.connect() as db:
            row = db.execute("SELECT id FROM users WHERE username=?", (username.lower(),)).fetchone()
            if row is None:
                raise ValueError("unknown user")
            db.execute("UPDATE users SET disabled=1 WHERE id=?", (row["id"],))
            db.execute("DELETE FROM logins WHERE user_id=?", (row["id"],))

    def reset_password(self, username: str, password: str) -> None:
        hashed = password_hash(password)
        with self.connect() as db:
            row = db.execute("SELECT id FROM users WHERE username=?", (username.lower(),)).fetchone()
            if row is None:
                raise ValueError("unknown user")
            db.execute("UPDATE users SET password=? WHERE id=?", (hashed, row["id"]))
            db.execute("DELETE FROM logins WHERE user_id=?", (row["id"],))

    @staticmethod
    def day() -> str:
        return datetime.now(timezone.utc).date().isoformat()

    def usage(self, user_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT calls, charged_tokens, actual_tokens FROM usage WHERE user_id=? AND day=?", (user_id, self.day())).fetchone()
        return {"day": self.day(), **(dict(row) if row else {"calls": 0, "charged_tokens": 0, "actual_tokens": 0})}

    def reserve(self, user_id: str, estimate: int, max_calls: int, max_tokens: int) -> str:
        day = self.day()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO usage VALUES (?, ?, 0, 0, 0)", (user_id, day))
            cursor = db.execute("UPDATE usage SET calls=calls+1, charged_tokens=charged_tokens+? WHERE user_id=? AND day=? AND calls<? AND charged_tokens+?<=?", (estimate, user_id, day, max_calls, estimate, max_tokens))
            if cursor.rowcount != 1:
                raise ProviderError("daily model request/token quota exceeded; retry after UTC midnight", retryable=False)
        return day

    def settle(self, user_id: str, day: str, estimate: int, actual: int) -> None:
        # Unknown/error/cancelled usage keeps the reservation to prevent free retries.
        if actual <= 0:
            return
        with self.connect() as db:
            db.execute("UPDATE usage SET charged_tokens=charged_tokens+?, actual_tokens=actual_tokens+? WHERE user_id=? AND day=?", (actual - estimate, actual, user_id, day))


class MeteredProvider:
    def __init__(self, provider, accounts: Accounts, user_id: str, *, max_calls: int, max_tokens: int) -> None:
        self.provider = provider
        self.id = provider.id
        self.accounts = accounts
        self.user_id = user_id
        self.max_calls = max_calls
        self.max_tokens = max_tokens

    async def request(self, model, request, on_text=None):
        if request.max_tokens is None:
            request = request.model_copy(update={"max_tokens": 4096})
        # UTF-8 bytes plus protocol headroom is deliberately conservative without a provider tokenizer.
        estimate = len(request.model_dump_json().encode()) + request.max_tokens + 1024
        day = self.accounts.reserve(self.user_id, estimate, self.max_calls, self.max_tokens)
        stream = getattr(self.provider, "complete_stream", None)
        response = await stream(model, request, on_text) if stream and on_text else await self.provider.complete(model, request)
        actual = response.usage.total_tokens or response.usage.input_tokens + response.usage.output_tokens
        self.accounts.settle(self.user_id, day, estimate, actual)
        return response

    async def complete(self, model, request):
        return await self.request(model, request)

    async def complete_stream(self, model, request, on_text):
        return await self.request(model, request, on_text)

    async def aclose(self):
        close = getattr(self.provider, "aclose", None)
        if close:
            await close()
