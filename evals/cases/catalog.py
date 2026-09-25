from __future__ import annotations

from evals.cases.model import EvalCase
from evals.cases.smoke import SMOKE_CASES
from evals.cases.standard import STANDARD_CASES


ALL_CASES: tuple[EvalCase, ...] = (
    *SMOKE_CASES,
    *STANDARD_CASES,
)