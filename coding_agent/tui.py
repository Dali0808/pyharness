from __future__ import annotations

import argparse
import asyncio
import os
import platform
import subprocess
import tempfile
from dataclasses import replace
from io import StringIO
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from agent.events import (
    AgentEvent,
    ModelRequested,
    ModelResponded,
    ModelTextDelta,
    RunFailed,
    RunFinished,
    ToolFinished,
    ToolStarted,
    SubtaskStarted,
    SubtaskFinished,
)
from coding_agent.builtins import resolve_workspace_path
from coding_agent.command import format_command
from coding_agent.cli import (
    CliConfig,
    DEFAULT_SYSTEM_PROMPT,
    assistant_text,
    close_provider,
    create_provider,
    render_event,
    run_task,
)


class TuiSettings(BaseModel):
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


def _load_settings(workspace: Path) -> TuiSettings:
    path = _settings_path(workspace)
    if not path.exists():
        return TuiSettings()
    if path.stat().st_size > 16_384:
        raise ValueError("settings file is too large")
    return TuiSettings.model_validate_json(path.read_bytes())


def _save_settings(config: CliConfig) -> None:
    path = _settings_path(config.workspace)
    values = {
        field: getattr(config, field)
        for field in TuiSettings.model_fields
        if field != "session"
    }
    values["session"] = str(config.session_path.relative_to(config.workspace))
    settings = TuiSettings.model_validate(values)
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


def parse_tui_args(argv: Sequence[str] | None = None) -> CliConfig:
    parser = argparse.ArgumentParser(
        description="Run the pyharness Textual interface."
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
        values = _load_settings(workspace).model_dump()
        for field in (
            "provider_id", "model_id", "base_url", "api_key_env", "max_steps", "context_window"
        ):
            override = getattr(args, "model" if field == "model_id" else field)
            if override is not None:
                values[field] = override
        settings = TuiSettings.model_validate(values)
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


def _display_workspace_path(path: Path) -> str:
    try:
        return str(Path("~") / path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def _display_session_path(config: CliConfig) -> str:
    try:
        return str(config.session_path.relative_to(config.workspace))
    except ValueError:
        return str(config.session_path)


def create_app(
    config: CliConfig,
    *,
    provider: Any | None = None,
) -> Any:
    try:
        from textual.app import App, ComposeResult
        from textual.containers import Horizontal, Vertical
        from textual.events import Key
        from textual.message import Message
        from textual.selection import Selection
        from textual.screen import ModalScreen
        from textual.strip import Strip
        from textual.theme import Theme
        from textual.widgets import Button, Footer, Header, RichLog, Static, TextArea
        from rich.markdown import Markdown
        from rich.table import Table
        from rich.text import Text
    except ImportError as exc:
        raise RuntimeError(
            "The TUI requires Textual. Install project dependencies first."
        ) from exc

    class TaskTextArea(TextArea):
        class Submitted(Message):
            def __init__(self, text: str) -> None:
                super().__init__()
                self.text = text

        async def _on_key(self, event: Key) -> None:
            if event.key == "enter":
                event.stop()
                event.prevent_default()
                self.post_message(self.Submitted(self.text))
                return
            if event.key == "shift+enter":
                event.stop()
                event.prevent_default()
                self.insert("\n")
                return
            await super()._on_key(event)

    class SelectableLog(RichLog):
        def get_selection(self, selection: Selection) -> tuple[str, str] | None:
            return selection.extract("\n".join(line.text for line in self.lines)), "\n"

        def render_line(self, y: int) -> Strip:
            line = super().render_line(y)
            selection = self.screen.selections.get(self)
            if selection is None:
                return line
            span = selection.get_span(y + self.scroll_offset.y)
            if span is None:
                return line
            start, end = span
            start = max(0, start - self.scroll_offset.x)
            end = line.cell_length if end < 0 else max(start, end - self.scroll_offset.x)
            return Strip.join((
                line.crop(0, start),
                line.crop(start, end).apply_style(
                    self.screen.get_component_rich_style("screen--selection")
                ),
                line.crop(end),
            ))

    class ApprovalScreen(ModalScreen):
        def __init__(
            self,
            detail: str,
            *,
            title: str = "Review proposed change",
            description: str = "The agent wants to modify a workspace file.",
            action: str = "Apply",
        ) -> None:
            super().__init__()
            self.detail = detail
            self.title_text = title
            self.description = description
            self.action = action

        def compose(self) -> ComposeResult:
            yield Vertical(
                Static(self.title_text, classes="modal-title"),
                Static(self.description, classes="modal-copy"),
                RichLog(id="diff", markup=False),
                Horizontal(
                    Button(self.action, id="apply", variant="success"),
                    Button("Reject", id="reject", variant="error"),
                    classes="modal-actions",
                ),
                id="approval-dialog",
            )

        def on_mount(self) -> None:
            self.query_one("#diff", RichLog).write(self.detail)

        def on_button_pressed(self, event: Button.Pressed) -> None:
            self.dismiss(event.button.id == "apply")

    class WorkspaceTrustScreen(ModalScreen[bool]):
        def compose(self) -> ComposeResult:
            yield Vertical(
                Static("Trust this workspace?", classes="modal-title"),
                Static(str(config.workspace), classes="modal-copy"),
                Static("The agent can read files and propose changes here.", classes="modal-copy"),
                Horizontal(
                    Button("Exit", id="decline"),
                    Button("Trust workspace", id="trust", variant="success"),
                    classes="modal-actions",
                ),
                id="trust-dialog",
            )

        def on_button_pressed(self, event: Button.Pressed) -> None:
            self.dismiss(event.button.id == "trust")

    class HarnessApp(App[None]):
        TITLE = "Lario"
        SUB_TITLE = "coding workspace"
        WELCOME_ART = "     ▐▛██▜▌\n    ▐ ●  ● ▌\n    ▐   3  ▌\n     ▝▜██▛▘"
        CSS = """
        Screen {
            layout: vertical;
            background: ansi_default;
        }
        Header {
            background: transparent;
            color: $text;
            dock: top;
            border-bottom: solid $primary-darken-2;
        }
        Footer {
            background: transparent;
            color: $text-muted;
        }
        #toolbar {
            height: 3;
            padding: 0 2;
            background: transparent;
        }
        #status {
            width: 1fr;
            color: $text-muted;
            content-align: left middle;
        }
        #stop {
            width: 10;
            min-width: 10;
            margin-left: 2;
        }
        #toggle-steps {
            width: 14;
            min-width: 14;
            margin-left: 1;
        }
        #stop, #toggle-steps {
            height: 3;
            color: $primary;
            background: transparent;
            border: round $primary;
            text-style: bold;
            content-align: center middle;
        }
        #stop:hover, #toggle-steps:hover {
            background: transparent;
            color: $secondary;
            text-style: bold underline;
        }
        #stop:disabled {
            border: round $primary !important;
        }
        #conversation {
            height: 1fr;
            padding: 0 2;
            background: transparent;
        }
        #welcome {
            height: 1fr;
            padding: 0 4;
            align: center middle;
            background: transparent;
            border: round $primary;
        }
        #welcome-brand, #welcome-info {
            height: 1fr;
            padding: 0 3;
            background: transparent;
        }
        #welcome-brand {
            width: 1fr;
        }
        #welcome-info {
            width: 1fr;
            margin-left: 2;
            border-left: solid $secondary-darken-1;
        }
        .welcome-title {
            height: 1;
            color: $primary;
            text-style: bold;
        }
        .welcome-heading {
            height: 1;
            color: $secondary;
            text-style: bold;
        }
        .welcome-copy {
            color: $text-muted;
        }
        #welcome-art {
            color: $warning;
        }
        #welcome-workspace {
            color: $text-muted;
        }
        #log {
            height: 1fr;
            padding: 1 2;
            border: round $primary-darken-2;
            background: transparent;
            scrollbar-color: $primary-darken-1;
        }
        #composer {
            height: 6;
            padding: 0 2;
            background: transparent;
        }
        #task {
            height: 6;
            border: round $primary;
            background: transparent;
        }
        #approval-dialog {
            width: 92%;
            height: 82%;
            padding: 2;
            background: transparent;
            border: round $primary;
        }
        #trust-dialog {
            width: 80%;
            height: 17;
            padding: 2;
            background: $surface;
            border: round $primary;
        }
        .modal-title {
            height: 2;
            color: $text;
            text-style: bold;
        }
        .modal-copy {
            height: 2;
            color: $text-muted;
        }
        .modal-actions {
            height: 3;
            align: right middle;
        }
        .modal-actions Button {
            margin-left: 1;
        }
        """
        BINDINGS = [
            ("ctrl+q", "quit", "Quit"),
        ]

        def __init__(self) -> None:
            super().__init__()
            self.register_theme(
                Theme(
                    name="lario-blue",
                    primary="#1683d8",
                    secondary="#4aa3df",
                    warning="#1683d8",
                    error="#c75c6b",
                    success="#4fb477",
                    accent="#1683d8",
                    foreground="ansi_default",
                    background="ansi_default",
                    surface="ansi_default",
                    panel="ansi_default",
                    ansi=True,
                    variables={
                        "ansi-background": "ansi_default",
                        "ansi-foreground": "ansi_default",
                        "footer-key-foreground": "#1683d8",
                        "button-focus-text-style": "bold underline",
                        "input-cursor-background": "#1683d8",
                    },
                )
            )
            self.theme = "lario-blue"
            self.config = config
            self.provider = provider
            self.current_task: asyncio.Task[Any] | None = None
            self._trusted = False
            self.show_steps = False
            self._log_entries: list[tuple[bool, Any]] = []
            self._live_text = ""
            self._live_entry_index: int | None = None

        def compose(self) -> ComposeResult:
            yield Header()
            yield Horizontal(
                Static(
                    f"session: {_display_session_path(self.config)}",
                    id="status",
                ),
                Button("Stop", id="stop", disabled=True),
                Button("Show steps", id="toggle-steps"),
                id="toolbar",
            )
            yield Vertical(
                Horizontal(
                    Vertical(
                        Static("Lario", classes="welcome-title"),
                        Static(self.WELCOME_ART, id="welcome-art", markup=False),
                        Static(
                            "Coding workspace.",
                            classes="welcome-copy",
                        ),
                        Static(
                            f"workspace: {_display_workspace_path(self.config.workspace)}",
                            id="welcome-workspace",
                        ),
                        id="welcome-brand",
                    ),
                    Vertical(
                        Static("Getting started", classes="welcome-title"),
                        Static("Describe a task below.", classes="welcome-copy"),
                        Static("Enter  submit task", classes="welcome-copy"),
                        Static("Shift+Enter  new line", classes="welcome-copy"),
                        Static("Show steps  view activity", classes="welcome-copy"),
                        Static("Recent activity: none", classes="welcome-heading"),
                        id="welcome-info",
                    ),
                    id="welcome",
                ),
                SelectableLog(id="log", highlight=True, markup=False),
                id="conversation",
            )
            yield Vertical(
                TaskTextArea(
                    placeholder="Describe what you want the coding agent to do...",
                    id="task",
                ),
                id="composer",
            )
            yield Footer()

        async def on_mount(self) -> None:
            self.query_one("#welcome").display = False
            self.query_one("#log", RichLog).display = False
            self.query_one("#composer").display = False
            self.query_one("#toggle-steps", Button).active_effect_duration = 0
            self.push_screen(WorkspaceTrustScreen(), self._on_trust)

        def _on_trust(self, trusted: bool | None) -> None:
            if not trusted:
                self.exit()
                return
            self._trusted = True
            self.query_one("#welcome").display = True
            self.query_one("#composer").display = True
            self.query_one("#task", TaskTextArea).focus()

        async def on_unmount(self) -> None:
            if self.current_task is not None:
                self.current_task.cancel()
                await asyncio.gather(self.current_task, return_exceptions=True)
            if self.provider is not None:
                await close_provider(self.provider)

        def on_task_text_area_submitted(
            self, event: TaskTextArea.Submitted
        ) -> None:
            task_input = self.query_one("#task", TaskTextArea)
            task = event.text.strip()
            if not self._trusted or not task or self.current_task is not None:
                return
            if task.startswith("/"):
                task_input.clear()
                self._handle_command(task)
                return
            if not self.config.model_id:
                self._set_status("set /model before sending a task")
                return
            task_input.clear()
            self.query_one("#welcome").display = False
            self.query_one("#log", RichLog).display = True
            self.current_task = asyncio.create_task(self._run(task))

        def _handle_command(self, task: str) -> None:
            command, _, value = task.partition(" ")
            value = value.strip()
            self.query_one("#welcome").display = False
            self.query_one("#log", RichLog).display = True
            if command == "/help":
                self._write("/settings  /model ID  /provider ID  /base-url URL  "
                            "/api-key-env NAME  /max-steps N  /context-window N|off  "
                            "/multi-agent on|off  /session PATH")
                return
            if command == "/settings":
                config = self.config
                self._write(
                    f"model: {config.model_id or '(unset)'}\n"
                    f"provider: {config.provider_id}\n"
                    f"base URL: {config.base_url}\n"
                    f"API key env: {config.api_key_env} "
                    f"({'set' if os.environ.get(config.api_key_env) else 'unset'})\n"
                    f"max steps: {config.max_steps}\n"
                    f"context window: {config.context_window or 'off'}\n"
                    f"multi-agent: {'on' if config.multi_agent else 'off'}\n"
                    f"session: {_display_session_path(config)}"
                )
                return
            if command == "/session":
                if not value:
                    self._write("Usage: /session WORKSPACE_RELATIVE_PATH")
                    return
                try:
                    path = resolve_workspace_path(self.config.workspace, value)
                    if path.is_dir():
                        raise ValueError("session path must be a file")
                    updated = replace(self.config, session_path=path)
                    _save_settings(updated)
                except (OSError, ValueError, ValidationError) as exc:
                    self._write(f"Invalid /session: {exc}")
                    return
                self.config = updated
                self._log_entries.clear()
                self._refresh_log()
                self.query_one("#log", RichLog).display = False
                self.query_one("#welcome").display = True
                self._set_status("ready")
                return
            field = {
                "/model": "model_id",
                "/provider": "provider_id",
                "/base-url": "base_url",
                "/api-key-env": "api_key_env",
                "/max-steps": "max_steps",
                "/context-window": "context_window",
                "/multi-agent": "multi_agent",
            }.get(command)
            if field is None:
                self._write(f"Unknown command: {command}. Use /help.")
                return
            if not value:
                self._write(f"Usage: {command} VALUE")
                return
            try:
                setting: str | int | None = value
                if field in {"max_steps", "context_window"}:
                    setting = None if field == "context_window" and value == "off" else int(value)
                if field == "multi_agent":
                    if value not in {"on", "off"}:
                        raise ValueError("use on or off")
                    setting = value == "on"
                if field in {"provider_id", "model_id"} and any(c.isspace() for c in value):
                    raise ValueError("value must not contain whitespace")
                updated = replace(self.config, **{field: setting})
                _save_settings(updated)
            except (OSError, ValueError, ValidationError) as exc:
                self._write(f"Invalid {command}: {exc}")
                return
            previous = self.config
            self.config = updated
            self._write(f"{command[1:]}: {value}")
            if (field in {"model_id", "provider_id", "context_window", "multi_agent"}
                    and getattr(previous, field) != setting
                    and updated.session_path.exists()):
                self._write("An existing session may require its original model; "
                            "use /session NEW_PATH for a new conversation.")

        async def _run(self, task: str) -> None:
            self._set_running(True)
            self._live_text = ""
            self._live_entry_index = None
            self._write(Text(
                "❯ " + task.replace("\n", "\n  "),
                style="#f4f4f4 on #303030",
                justify="left",
            ))
            self._write("")
            output = StringIO()

            def on_event(event: AgentEvent) -> None:
                if isinstance(event, ModelRequested):
                    self._live_text = ""
                    if self._live_entry_index is not None:
                        del self._log_entries[self._live_entry_index]
                        self._live_entry_index = None
                        self._refresh_log()
                if isinstance(event, ModelTextDelta):
                    self._live_text += event.text
                    message = "⏺ " + self._live_text.replace("\n", "\n  ")
                    if self._live_entry_index is None:
                        self._live_entry_index = len(self._log_entries)
                        self._write(message)
                    else:
                        self._log_entries[self._live_entry_index] = (False, message)
                        # ponytail: Redraws history per chunk; use per-message widgets if long sessions lag.
                        self._refresh_log()
                    return
                is_step_event = isinstance(
                    event, (ModelRequested, ModelResponded, ToolStarted, ToolFinished,
                            SubtaskStarted, SubtaskFinished)
                )
                if is_step_event:
                    self._write(render_event(event), step=True)
                    if isinstance(event, ToolFinished) and event.result.tool_name == "run_command":
                        self._write(f"command result:\n{event.result.content}")
                    return
                if isinstance(event, RunFinished):
                    answer = Table.grid(padding=(0, 1))
                    answer.add_row("⏺", Markdown(assistant_text(event.final_message)))
                    if self._live_entry_index is None:
                        self._write(answer)
                    else:
                        self._log_entries[self._live_entry_index] = (False, answer)
                        self._live_entry_index = None
                        self._refresh_log()
                    self._write("")
                    return
                self._write(render_event(event))
                if isinstance(event, RunFailed):
                    if self._live_text:
                        self._live_entry_index = None
                        self._write("partial answer (not saved)")
                    self._write(f"failed ({event.code}): {event.message}")

            async def approve(diff: str) -> bool:
                return await self._ask_approval(diff)

            async def approve_command(argv: list[str], cwd: Path) -> bool:
                return await self._ask_command_approval(argv, cwd)

            provider = self.provider
            try:
                if provider is None:
                    provider = create_provider(self.config)
                self.config.session_path.parent.mkdir(parents=True, exist_ok=True)
                config = replace(self.config, task=task)
                result = await run_task(
                    config,
                    provider,
                    output,
                    on_event=on_event,
                    approve=approve,
                    approve_command=approve_command,
                )
                for line in output.getvalue().splitlines():
                    if line.startswith("memory "):
                        self._write(line)
                if result.exit_code == 0:
                    self._set_status("ready")
                else:
                    self._set_status("run failed")
            except Exception as exc:
                self._write(f"TUI error: {exc}")
                self._set_status("TUI error")
            finally:
                if self.provider is None and provider is not None:
                    await close_provider(provider)
                self.current_task = None
                self._set_running(False)

        async def _ask_approval(self, diff: str) -> bool:
            loop = asyncio.get_running_loop()
            decision: asyncio.Future[bool] = loop.create_future()

            def finished(value: bool | None) -> None:
                if not decision.done():
                    decision.set_result(bool(value))

            self.push_screen(ApprovalScreen(diff), finished)
            return await decision

        async def _ask_command_approval(self, argv: list[str], cwd: Path) -> bool:
            loop = asyncio.get_running_loop()
            decision: asyncio.Future[bool] = loop.create_future()

            def finished(value: bool | None) -> None:
                if not decision.done():
                    decision.set_result(bool(value))

            self.push_screen(ApprovalScreen(
                format_command(argv, cwd),
                title="Run verification command",
                description="This command may modify files in the workspace.",
                action="Run",
            ), finished)
            return await decision

        def action_stop_run(self) -> None:
            if self.current_task is not None:
                self._set_status("stopping")
                self.current_task.cancel()

        def copy_to_clipboard(self, text: str) -> None:
            super().copy_to_clipboard(text)
            if platform.system() == "Darwin":
                subprocess.run(["pbcopy"], input=text, text=True, check=False)

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "stop":
                self.action_stop_run()
            elif event.button.id == "toggle-steps":
                self.show_steps = not self.show_steps
                event.button.label = (
                    "Hide steps" if self.show_steps else "Show steps"
                )
                self._refresh_log()

        def _write(self, message: Any, *, step: bool = False) -> None:
            self._log_entries.append((step, message))
            if not step or self.show_steps:
                self.query_one("#log", RichLog).write(
                    message, expand=isinstance(message, Text)
                )

        def _refresh_log(self) -> None:
            log = self.query_one("#log", RichLog)
            log.clear()
            for is_step, message in self._log_entries:
                if self.show_steps or not is_step:
                    log.write(message, expand=isinstance(message, Text))

        def _set_status(self, value: str) -> None:
            status = self.query_one("#status", Static)
            status.update(
                f"session: {_display_session_path(self.config)} | {value}"
            )

        def _set_running(self, running: bool) -> None:
            self.query_one("#stop", Button).disabled = not running
            self.query_one("#task", TaskTextArea).disabled = running

    return HarnessApp()


def main(argv: Sequence[str] | None = None) -> None:
    config = parse_tui_args(argv)
    try:
        app = create_app(config)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    app.run()


def _positive_int(value: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if result <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return result


if __name__ == "__main__":
    main()
