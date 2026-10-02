from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
from io import StringIO
from pathlib import Path
from typing import Any, Sequence

from agent.events import (
    AgentEvent,
    ModelRequested,
    ModelResponded,
    ModelTextDelta,
    RunFailed,
    RunFinished,
    ToolFinished,
    ToolStarted,
)
from coding_agent.builtins import resolve_workspace_path
from coding_agent.command import format_command
from coding_agent.cli import (
    CliConfig,
    assistant_text,
    close_provider,
    create_provider,
    render_event,
    run_task,
)


def parse_tui_args(argv: Sequence[str] | None = None) -> CliConfig:
    parser = argparse.ArgumentParser(
        description="Run the pyharness Textual interface."
    )
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--session", default=".runtime/session.jsonl")
    parser.add_argument("--provider-id", default="openai")
    parser.add_argument("--model", required=True)
    parser.add_argument("--context-window", type=_positive_int, default=None)
    parser.add_argument("--base-url", default="https://api.openai.com/v1")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument(
        "--system-prompt",
        default=(
            "You are a coding assistant. Work only through the available "
            "workspace tools."
        ),
    )
    parser.add_argument("--max-steps", type=_positive_int, default=10)
    args = parser.parse_args(argv)

    workspace = Path(args.workspace).resolve(strict=False)
    if not workspace.is_dir():
        parser.error(f"workspace is not a directory: {workspace}")
    try:
        session_path = resolve_workspace_path(workspace, args.session)
    except (OSError, ValueError) as exc:
        parser.error(f"invalid session path: {exc}")
    session_path.parent.mkdir(parents=True, exist_ok=True)

    return CliConfig(
        task="",
        workspace=workspace,
        provider_id=args.provider_id,
        model_id=args.model,
        base_url=args.base_url,
        api_key_env=args.api_key_env,
        system_prompt=args.system_prompt,
        max_steps=args.max_steps,
        session_path=session_path,
        context_window=args.context_window,
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
        from textual.screen import ModalScreen
        from textual.theme import Theme
        from textual.widgets import Button, Footer, Header, RichLog, Static, TextArea
        from rich.markdown import Markdown
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
        #live-response {
            height: auto;
            max-height: 10;
            padding: 0 2;
            overflow-y: auto;
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
            ("ctrl+c", "stop_run", "Stop"),
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
            self.show_steps = False
            self._log_entries: list[tuple[bool, Any]] = []
            self._live_text = ""

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
                RichLog(id="log", highlight=True, markup=False),
                Static("", id="live-response"),
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
            if self.provider is None:
                self.provider = create_provider(self.config)
            self.query_one("#log", RichLog).display = False
            self.query_one("#live-response", Static).display = False
            self.query_one("#toggle-steps", Button).active_effect_duration = 0
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
            if not task or self.current_task is not None:
                return
            task_input.clear()
            self.query_one("#welcome").display = False
            self.query_one("#log", RichLog).display = True
            self.current_task = asyncio.create_task(self._run(task))

        async def _run(self, task: str) -> None:
            self._set_running(True)
            self._live_text = ""
            self.query_one("#live-response", Static).display = False
            self._write(f"> {task}")
            output = StringIO()

            def on_event(event: AgentEvent) -> None:
                if isinstance(event, ModelRequested):
                    self._live_text = ""
                    self.query_one("#live-response", Static).display = False
                if isinstance(event, ModelTextDelta):
                    self._live_text += event.text
                    live = self.query_one("#live-response", Static)
                    live.update(self._live_text)
                    live.display = True
                    live.scroll_end(animate=False)
                    return
                is_step_event = isinstance(
                    event, (ModelRequested, ModelResponded, ToolStarted, ToolFinished)
                )
                if is_step_event:
                    self._write(render_event(event), step=True)
                    if isinstance(event, ToolFinished) and event.result.tool_name == "run_command":
                        self._write(f"command result:\n{event.result.content}")
                    return
                if isinstance(event, RunFinished):
                    self.query_one("#live-response", Static).display = False
                    self._write("answer:")
                    self._write(Markdown(assistant_text(event.final_message)))
                    return
                self._write(render_event(event))
                if isinstance(event, RunFailed):
                    if self._live_text:
                        self.query_one("#live-response", Static).display = False
                        self._write("partial answer (not saved):")
                        self._write(self._live_text)
                    self._write(f"failed ({event.code}): {event.message}")

            async def approve(diff: str) -> bool:
                return await self._ask_approval(diff)

            async def approve_command(argv: list[str], cwd: Path) -> bool:
                return await self._ask_command_approval(argv, cwd)

            try:
                config = replace(self.config, task=task)
                result = await run_task(
                    config,
                    self.provider,
                    output,
                    on_event=on_event,
                    approve=approve,
                    approve_command=approve_command,
                )
                if result.exit_code == 0:
                    self._set_status("ready")
                else:
                    self._set_status("run failed")
            except Exception as exc:
                self._write(f"TUI error: {exc}")
                self._set_status("TUI error")
            finally:
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
                self.query_one("#log", RichLog).write(message)

        def _refresh_log(self) -> None:
            log = self.query_one("#log", RichLog)
            log.clear()
            for is_step, message in self._log_entries:
                if self.show_steps or not is_step:
                    log.write(message)

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
