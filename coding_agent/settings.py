from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path
from typing import Sequence
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from coding_agent.builtins import resolve_workspace_path
from coding_agent.cli import CliConfig, DEFAULT_SYSTEM_PROMPT


class WorkspaceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    provider_id: str = Field(default="openai", min_length=1)
    model_id: str = ""
    base_url: str = "https://api.openai.com/v1"
    api_key_env: str = Field(default="OPENAI_API_KEY", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    max_steps: int = Field(default=10, gt=0)
    context_window: int | None = Field(default=None, gt=0)
    session: str = Field(default=".runtime/session.jsonl", min_length=1)
    multi_agent: bool = False

    @field_validator("base_url")
    @classmethod
    def valid_base_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("base URL must be http(s) without credentials, query, or fragment")
        return value


def _settings_path(workspace: Path) -> Path:
    return resolve_workspace_path(workspace, ".runtime/lario.json")


def load_settings(workspace: Path) -> WorkspaceSettings:
    path = _settings_path(workspace)
    if not path.exists():
        return WorkspaceSettings()
    if path.stat().st_size > 16_384:
        raise ValueError("settings file is too large")
    return WorkspaceSettings.model_validate_json(path.read_bytes())


def save_settings(config: CliConfig) -> None:
    path = _settings_path(config.workspace)
    values = {
        field: getattr(config, field)
        for field in WorkspaceSettings.model_fields
        if field != "session"
    }
    values["session"] = str(config.session_path.relative_to(config.workspace))
    settings = WorkspaceSettings.model_validate(values)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", dir=path.parent, encoding="utf-8", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(settings.model_dump_json(indent=2) + "\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def parse_interface_args(
    argv: Sequence[str] | None = None, *, description: str = "Run the pyharness Textual interface.",
) -> CliConfig:
    parser = argparse.ArgumentParser(
        description=description
    )
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--session")
    parser.add_argument("--provider-id")
    parser.add_argument("--model")
    parser.add_argument("--context-window", type=_positive_int, default=None)
    parser.add_argument("--base-url")
    parser.add_argument("--api-key-env")
    parser.add_argument(
        "--system-prompt",
        default=DEFAULT_SYSTEM_PROMPT,
    )
    parser.add_argument("--max-steps", type=_positive_int)
    parser.add_argument("--multi-agent", action="store_true")
    args = parser.parse_args(argv)

    workspace = Path(args.workspace).resolve(strict=False)
    if not workspace.is_dir():
        parser.error(f"workspace is not a directory: {workspace}")
    try:
        values = load_settings(workspace).model_dump()
        for field in (
            "provider_id", "model_id", "base_url", "api_key_env", "max_steps", "context_window"
        ):
            override = getattr(args, "model" if field == "model_id" else field)
            if override is not None:
                values[field] = override
        settings = WorkspaceSettings.model_validate(values)
        if args.multi_agent:
            settings.multi_agent = True
        session_path = resolve_workspace_path(workspace, args.session or settings.session)
        if session_path.is_dir():
            raise ValueError("session path must be a file")
    except (OSError, ValueError, ValidationError) as exc:
        parser.error(f"invalid TUI configuration: {exc}")

    return CliConfig(
        task="",
        workspace=workspace,
        provider_id=settings.provider_id,
        model_id=settings.model_id,
        base_url=settings.base_url,
        api_key_env=settings.api_key_env,
        system_prompt=args.system_prompt,
        max_steps=settings.max_steps,
        session_path=session_path,
        context_window=settings.context_window,
        multi_agent=settings.multi_agent,
    )


def _positive_int(value: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if result <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return result
