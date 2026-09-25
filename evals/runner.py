from __future__ import annotations

import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from types import MappingProxyType
from typing import TypeAlias

from agent.events import AgentEvent, ToolFinished
from ai.provider import LLMProvider
from ai.schemas import ToolResultMessage
from coding_agent.cli import (
    CliConfig,
    TaskRunResult,
    close_provider,
    run_task,
)
from evals.cases.model import EvalCase


ProviderFactory: TypeAlias = Callable[[CliConfig], LLMProvider]


@dataclass(frozen=True, slots=True)
class EvalRunResult:
    case_id: str
    task_result: TaskRunResult
    elapsed_seconds: float
    final_files: Mapping[str, str]
    tool_errors: tuple[ToolResultMessage, ...]


class EvalRunner:
    async def run_case(
        self,
        case: EvalCase,
        provider_factory: ProviderFactory,
    ) -> EvalRunResult:
        with tempfile.TemporaryDirectory(
            prefix="pyharness-eval-",
        ) as directory:
            workspace = Path(directory)
            self._seed_workspace(
                workspace,
                case.initial_files,
            )

            config = CliConfig(
                task=case.task,
                workspace=workspace,
                provider_id="scripted",
                model_id="eval-model",
                base_url="http://eval.invalid",
                api_key_env="PYHARNESS_EVAL_API_KEY",
                system_prompt=(
                    "You are a coding assistant. Work only "
                    "through the available workspace tools."
                ),
                max_steps=10,
                session_path=None,
                context_window=None,
            )

            provider = provider_factory(config)
            tool_errors: list[ToolResultMessage] = []
            started_at = time.perf_counter()

            def collect_event(event: AgentEvent) -> None:
                if (
                        isinstance(event, ToolFinished)
                        and event.result.is_error
                ):
                    tool_errors.append(event.result)

            try:
                task_result = await run_task(
                    config,
                    provider,
                    StringIO(),
                    on_event=collect_event,
                )
            finally:
                await close_provider(provider)

            elapsed_seconds = (
                time.perf_counter() - started_at
            )
            final_files = self._snapshot_files(workspace)

            return EvalRunResult(
                case_id=case.case_id,
                task_result=task_result,
                elapsed_seconds=elapsed_seconds,
                final_files=final_files,
                tool_errors=tuple(tool_errors),
            )

    @staticmethod
    def _seed_workspace(
        workspace: Path,
        files: Mapping[str, str],
    ) -> None:
        for relative_path, content in files.items():
            path = workspace / relative_path
            path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            path.write_text(
                content,
                encoding="utf-8",
            )

    @staticmethod
    def _snapshot_files(
        workspace: Path,
    ) -> Mapping[str, str]:
        files: dict[str, str] = {}

        for path in sorted(workspace.rglob("*")):
            if not path.is_file():
                continue

            relative_path = path.relative_to(workspace)
            files[relative_path.as_posix()] = path.read_text(
                encoding="utf-8",
            )

        return MappingProxyType(files)