from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from statistics import median
from datetime import datetime, timezone

from coding_agent.cli import create_provider
from evals.api import EvaluationRun, run_evaluation
from evals.cases.catalog import ALL_CASES
from evals.cases.model import EvalCase
from evals.cases.research import RESEARCH_CASES
from evals.reports import TokenPricing, build_report
from evals.runner import EvalRunnerConfig, ProviderFactory


@dataclass(frozen=True, slots=True)
class Comparison:
    single: EvaluationRun
    multi: EvaluationRun
    config: EvalRunnerConfig
    evaluated_at: str

    def summary(self) -> dict[str, object]:
        single = self.single.report
        multi = self.multi.report
        single_wins = multi_wins = ties = 0
        for left, right in zip(single.cases, multi.cases, strict=True):
            if left.case_id != right.case_id:
                raise ValueError("paired cases are not aligned")
            if left.passed and not right.passed:
                single_wins += 1
            elif right.passed and not left.passed:
                multi_wins += 1
            else:
                ties += 1

        def metrics(run: EvaluationRun) -> dict[str, object]:
            report = run.report
            passed = report.summary.passed_cases
            cost = report.summary.total_estimated_cost
            return {
                "passed": passed,
                "success_rate": report.summary.success_rate,
                "total_tokens": report.summary.usage.total_tokens,
                "total_cost": cost,
                "cost_per_success": cost / passed if cost is not None and passed else None,
                "median_elapsed_seconds": (
                    median(case.elapsed_seconds for case in report.cases)
                    if report.cases else None
                ),
                "subtasks": sum(result.run_result.subtask_count for result in run.results),
            }

        return {
            "evaluated_at": self.evaluated_at,
            "model": f"{self.config.provider_id}/{self.config.model_id}",
            "max_steps": self.config.max_steps,
            "context_window": self.config.context_window,
            "cases": len(single.cases),
            "single": metrics(self.single),
            "multi": metrics(self.multi),
            "single_only_successes": single_wins,
            "multi_only_successes": multi_wins,
            "ties": ties,
            "paired_results": [
                {
                    "case_id": left.case_id,
                    "single_success": left.passed,
                    "multi_success": right.passed,
                    "single_tokens": left.usage.total_tokens,
                    "multi_tokens": right.usage.total_tokens,
                    "single_cost": left.estimated_cost,
                    "multi_cost": right.estimated_cost,
                    "single_elapsed_seconds": left.elapsed_seconds,
                    "multi_elapsed_seconds": right.elapsed_seconds,
                }
                for left, right in zip(single.cases, multi.cases, strict=True)
            ],
        }


async def run_comparison(
    provider_factory: ProviderFactory,
    *,
    cases: Sequence[EvalCase] = ALL_CASES,
    config: EvalRunnerConfig | None = None,
    pricing: TokenPricing | None = None,
) -> Comparison:
    selected = tuple(cases)
    base = config or EvalRunnerConfig()
    single_results = []
    multi_results = []
    for index, case in enumerate(selected):
        order = (False, True) if index % 2 == 0 else (True, False)
        for mode in order:
            run = await run_evaluation(
                provider_factory,
                cases=(case,),
                runner_config=replace(base, multi_agent=mode),
                pricing=pricing,
            )
            (multi_results if mode else single_results).extend(run.results)
    return Comparison(
        single=EvaluationRun(tuple(single_results), build_report(single_results, pricing=pricing)),
        multi=EvaluationRun(tuple(multi_results), build_report(multi_results, pricing=pricing)),
        config=base,
        evaluated_at=datetime.now(timezone.utc).isoformat(),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare single and multi-agent runs.")
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key-env", required=True)
    parser.add_argument("--provider-id", default="openai")
    parser.add_argument("--max-steps", type=int, default=10)
    parser.add_argument("--context-window", type=int)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case-set", choices=("catalog", "research"), default="catalog")
    parser.add_argument("--input-price", type=float)
    parser.add_argument("--cached-input-price", type=float)
    parser.add_argument("--output-price", type=float)
    args = parser.parse_args()
    if args.max_steps <= 0 or (args.context_window is not None and args.context_window <= 0):
        parser.error("step and context limits must be positive")
    rates = (args.input_price, args.cached_input_price, args.output_price)
    if any(rate is not None for rate in rates) and not all(rate is not None for rate in rates):
        parser.error("all three token prices are required together")
    pricing = TokenPricing(*rates) if all(rate is not None for rate in rates) else None
    result = asyncio.run(run_comparison(
        create_provider,
        cases=RESEARCH_CASES if args.case_set == "research" else ALL_CASES,
        config=EvalRunnerConfig(
            provider_id=args.provider_id,
            model_id=args.model,
            base_url=args.base_url,
            api_key_env=args.api_key_env,
            max_steps=args.max_steps,
            context_window=args.context_window,
        ),
        pricing=pricing,
    ))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result.summary(), indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
