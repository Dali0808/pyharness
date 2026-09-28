from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
from io import StringIO
from pathlib import Path
from typing import Any, Sequence

from agent.events import AgentEvent, RunFailed, RunFinished
from coding_agent.builtins import resolve_workspace_path
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
        from textual.widgets import Button, Footer, Header, RichLog, Static, TextArea
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
        def __init__(self, diff: str) -> None:
            super().__init__()
            self.diff = diff

        def compose(self) -> ComposeResult:
            yield Vertical(
                Static("Review proposed change", classes="modal-title"),
                RichLog(id="diff", markup=False),
                Horizontal(
                    Button("Apply", id="apply", variant="success"),
                    Button("Reject", id="reject", variant="error"),
                    classes="modal-actions",
                ),
                id="approval-dialog",
            )

        def on_mount(self) -> None:
            self.query_one("#diff", RichLog).write(self.diff)

        def on_button_pressed(self, event: Button.Pressed) -> None:
            self.dismiss(event.button.id == "apply")

    class HarnessApp(App[None]):
        CSS = """
        Screen { layout: vertical; }
        #toolbar { height: 3; padding: 1; }
        #status { width: 1fr; }
        #stop { width: 12; }
        #log { height: 1fr; border: solid $surface-lighten-2; }
        #task { dock: bottom; height: 6; margin: 1; }
        #approval-dialog { width: 90%; height: 80%; padding: 1; background: $surface; }
        #diff { height: 1fr; border: solid $surface-lighten-2; }
        .modal-title { height: 2; text-style: bold; }
        .modal-actions { height: 3; align: right middle; }
        """
        BINDINGS = [
            ("ctrl+c", "stop_run", "Stop"),
            ("ctrl+q", "quit", "Quit"),
        ]

        def __init__(self) -> None:
            super().__init__()
            self.config = config
            self.provider = provider
            self.current_task: asyncio.Task[Any] | None = None

        def compose(self) -> ComposeResult:
            yield Header()
            yield Horizontal(
                Static(
                    f"workspace: {self.config.workspace} | "
                    f"session: {self.config.session_path}",
                    id="status",
                ),
                Button("Stop", id="stop", disabled=True),
                id="toolbar",
            )
            yield RichLog(id="log", highlight=True, markup=False)
            yield TaskTextArea(
                placeholder=(
                    "Describe the next coding task. "
                    "Enter submits; Shift+Enter adds a new line."
                ),
                id="task",
            )
            yield Footer()

        async def on_mount(self) -> None:
            if self.provider is None:
                self.provider = create_provider(self.config)
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
            self.current_task = asyncio.create_task(self._run(task))

        async def _run(self, task: str) -> None:
            self._set_running(True)
            self._write(f"> {task}")
            output = StringIO()

            def on_event(event: AgentEvent) -> None:
                self._write(render_event(event))
                if isinstance(event, RunFinished):
                    self._write(f"answer: {assistant_text(event.final_message)}")
                elif isinstance(event, RunFailed):
                    self._write(f"failed ({event.code}): {event.message}")

            async def approve(diff: str) -> bool:
                return await self._ask_approval(diff)

            try:
                config = replace(self.config, task=task)
                result = await run_task(
                    config,
                    self.provider,
                    output,
                    on_event=on_event,
                    approve=approve,
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

        def action_stop_run(self) -> None:
            if self.current_task is not None:
                self._set_status("stopping")
                self.current_task.cancel()

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "stop":
                self.action_stop_run()

        def _write(self, message: str) -> None:
            self.query_one("#log", RichLog).write(message)

        def _set_status(self, value: str) -> None:
            status = self.query_one("#status", Static)
            status.update(
                f"workspace: {self.config.workspace} | "
                f"session: {self.config.session_path} | {value}"
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
