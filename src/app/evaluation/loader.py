"""Version-controlled JSONL evaluation dataset loading and validation."""

import hashlib
import json
from pathlib import Path

from pydantic import ValidationError

from app.evaluation.models import EvaluationCase, EvaluationSplit

DEFAULT_EVALUATION_ROOT = Path("evals")


class EvaluationDataError(RuntimeError):
    """Raised when version-controlled evaluation data is invalid."""


def split_path(
    split: EvaluationSplit,
    *,
    root: Path = DEFAULT_EVALUATION_ROOT,
) -> Path:
    return root / split.value / "rag_cases.jsonl"


def load_evaluation_cases(
    split: EvaluationSplit,
    *,
    root: Path = DEFAULT_EVALUATION_ROOT,
) -> tuple[EvaluationCase, ...]:
    path = split_path(split, root=root)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise EvaluationDataError(f"Evaluation split could not be read: {split.value}") from exc

    cases: list[EvaluationCase] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            case = EvaluationCase.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise EvaluationDataError(
                f"Invalid {split.value} evaluation case at line {line_number}."
            ) from exc
        if case.split is not split:
            raise EvaluationDataError(
                f"Case {case.id} declares split {case.split.value}, expected {split.value}."
            )
        cases.append(case)

    if not cases:
        raise EvaluationDataError(f"Evaluation split is empty: {split.value}")
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise EvaluationDataError(f"Evaluation split contains duplicate IDs: {split.value}")
    return tuple(cases)


def validate_all_splits(*, root: Path = DEFAULT_EVALUATION_ROOT) -> None:
    development = load_evaluation_cases(EvaluationSplit.DEVELOPMENT, root=root)
    holdout = load_evaluation_cases(EvaluationSplit.HOLDOUT, root=root)
    development_ids = {case.id for case in development}
    holdout_ids = {case.id for case in holdout}
    if development_ids & holdout_ids:
        raise EvaluationDataError("Development and holdout case IDs must be disjoint.")


def evaluation_dataset_fingerprint(cases: tuple[EvaluationCase, ...]) -> str:
    """Hash ordered, canonical case content for safe checkpoint compatibility."""

    canonical = json.dumps(
        [case.model_dump(mode="json") for case in cases],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
