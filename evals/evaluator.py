from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from evals.cases.model import EvalCase
from evals.runner import (
    EvalRunResult,
    EvalRunner,
    ProviderFactory,
)
from evals.scorers import ScoreResult, score_case


@dataclass(frozen=True, slots=True)
class CaseEvaluationResult:
    case_id: str
    run_result: EvalRunResult
    score: ScoreResult


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

        return CaseEvaluationResult(
            case_id=case.case_id,
            run_result=run_result,
            score=score,
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