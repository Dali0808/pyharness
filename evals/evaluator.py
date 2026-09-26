from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from evals.cases.model import EvalCase
from evals.runner import (
    EvalRunResult,
    EvalRunner,
    ProviderFactory,
)
from evals.scorers import (
    ScoreResult,
    ToolCoverageResult,
    score_case,
    score_file_contents,
    score_tool_coverage,
    run_succeeded,
)


@dataclass(frozen=True, slots=True)
class CaseEvaluationResult:
    case_id: str
    run_result: EvalRunResult
    score: ScoreResult
    artifact_score: ScoreResult
    tool_coverage: ToolCoverageResult

    @property
    def run_success(self) -> bool:
        return run_succeeded(self.run_result)

    @property
    def task_success(self) -> bool:
        return self.score.passed


class Evaluator:
    def __init__(
        self,
        runner: EvalRunner | None = None,
    ) -> None:
        self._runner = runner or EvalRunner()

    async def evaluate_case(
        self,
        case: EvalCase,
        provider_factory: ProviderFactory,
    ) -> CaseEvaluationResult:
        run_result = await self._runner.run_case(
            case,
            provider_factory,
        )
        score = score_case(case, run_result)
        artifact_score = (
            score_file_contents(run_result, case.expected_files)
            if case.expected_files
            else ScoreResult(
                passed=False,
                score=0.0,
                reason="case has no deterministic expected files",
            )
        )

        return CaseEvaluationResult(
            case_id=case.case_id,
            run_result=run_result,
            score=score,
            artifact_score=artifact_score,
            tool_coverage=score_tool_coverage(case, run_result),
        )

    async def evaluate_cases(
        self,
        cases: Sequence[EvalCase],
        provider_factory: ProviderFactory,
    ) -> list[CaseEvaluationResult]:
        return [
            await self.evaluate_case(
                case,
                provider_factory,
            )
            for case in cases
        ]
