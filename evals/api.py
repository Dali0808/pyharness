from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from evals.cases.catalog import ALL_CASES
from evals.cases.model import EvalCase
from evals.evaluator import (
    CaseEvaluationResult,
    Evaluator,
)
from evals.reports import (
    EvaluationReport,
    TokenPricing,
    build_report,
    write_reports,
)
from evals.runner import ProviderFactory


@dataclass(frozen=True, slots=True)
class EvaluationRun:
    results: tuple[CaseEvaluationResult, ...]
    report: EvaluationReport


async def run_evaluation(
    provider_factory: ProviderFactory,
    *,
    cases: Sequence[EvalCase] = ALL_CASES,
    pricing: TokenPricing | None = None,
    json_path: Path | None = None,
    markdown_path: Path | None = None,
    evaluator: Evaluator | None = None,
) -> EvaluationRun:
    if (json_path is None) != (markdown_path is None):
        raise ValueError(
            "json_path and markdown_path must be provided together"
        )

    active_evaluator = evaluator or Evaluator()
    selected_cases = tuple(cases)
    results = tuple(
        await active_evaluator.evaluate_cases(
            selected_cases,
            provider_factory,
        )
    )
    report = build_report(
        results,
        pricing=pricing,
    )

    if json_path is not None:
        write_reports(
            report,
            json_path=json_path,
            markdown_path=markdown_path,
        )

    return EvaluationRun(
        results=results,
        report=report,
    )