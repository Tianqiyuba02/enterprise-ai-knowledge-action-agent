"""Load the V4 development set only. No V4 holdout loader exists."""

import json
from pathlib import Path

from pydantic import ValidationError

from app.evaluation.loader import DEFAULT_EVALUATION_ROOT, EvaluationDataError
from app.evaluation.v4.fingerprints import sha256_json
from app.evaluation.v4.models import V4CaseSplit, V4ProductEvaluationCase

DEVELOPMENT_CASES_NAME = "v4_product_cases.jsonl"
EXPECTED_DEVELOPMENT_CASE_COUNT = 16


def v4_development_path(*, root: Path = DEFAULT_EVALUATION_ROOT) -> Path:
    return root / V4CaseSplit.DEVELOPMENT.value / DEVELOPMENT_CASES_NAME


def load_v4_development_cases(
    *,
    root: Path = DEFAULT_EVALUATION_ROOT,
) -> tuple[V4ProductEvaluationCase, ...]:
    path = v4_development_path(root=root)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise EvaluationDataError("V4 development evaluation cases could not be read.") from exc

    cases: list[V4ProductEvaluationCase] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            case = V4ProductEvaluationCase.model_validate(json.loads(line))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise EvaluationDataError(
                f"Invalid V4 development case at line {line_number}."
            ) from exc
        cases.append(case)

    if len(cases) != EXPECTED_DEVELOPMENT_CASE_COUNT:
        raise EvaluationDataError(
            f"V4 development set must contain exactly {EXPECTED_DEVELOPMENT_CASE_COUNT} cases."
        )
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise EvaluationDataError("V4 development set contains duplicate case IDs.")
    if any(case.dataset_kind != "DEVELOPMENT" for case in cases):
        raise EvaluationDataError("V4 development cases must be marked DEVELOPMENT.")
    return tuple(cases)


def v4_dataset_fingerprint(cases: tuple[V4ProductEvaluationCase, ...]) -> str:
    return sha256_json([case.model_dump(mode="json") for case in cases])


def assert_no_v4_holdout(*, root: Path = DEFAULT_EVALUATION_ROOT) -> None:
    holdout_dir = root / "holdout"
    forbidden = (
        holdout_dir / DEVELOPMENT_CASES_NAME,
        holdout_dir / "v4_product_cases.jsonl",
        holdout_dir / "v4-holdout-cases.jsonl",
        holdout_dir / "frozen-v4-cases.jsonl",
    )
    existing = [str(path) for path in forbidden if path.exists()]
    if existing:
        raise EvaluationDataError("V4 holdout files must not exist.")
