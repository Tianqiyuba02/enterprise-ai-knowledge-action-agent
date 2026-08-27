"""Version-controlled V3 agent dataset loading and fingerprinting."""

import hashlib
import json
from pathlib import Path

from pydantic import ValidationError

from app.evaluation.agent_models import AgentEvaluationCase
from app.evaluation.loader import DEFAULT_EVALUATION_ROOT, EvaluationDataError
from app.evaluation.models import EvaluationSplit


def agent_split_path(
    split: EvaluationSplit,
    *,
    root: Path = DEFAULT_EVALUATION_ROOT,
) -> Path:
    return root / split.value / "agent_cases.jsonl"


def load_agent_evaluation_cases(
    split: EvaluationSplit,
    *,
    root: Path = DEFAULT_EVALUATION_ROOT,
) -> tuple[AgentEvaluationCase, ...]:
    path = agent_split_path(split, root=root)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise EvaluationDataError(
            f"Agent evaluation split could not be read: {split.value}"
        ) from exc

    cases: list[AgentEvaluationCase] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            case = AgentEvaluationCase.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise EvaluationDataError(
                f"Invalid {split.value} agent evaluation case at line {line_number}."
            ) from exc
        if case.split is not split:
            raise EvaluationDataError(
                f"Agent case {case.id} declares split {case.split.value}, expected {split.value}."
            )
        cases.append(case)

    if not cases:
        raise EvaluationDataError(f"Agent evaluation split is empty: {split.value}")
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise EvaluationDataError(f"Agent evaluation split contains duplicate IDs: {split.value}")
    return tuple(cases)


def validate_all_agent_splits(*, root: Path = DEFAULT_EVALUATION_ROOT) -> None:
    development = load_agent_evaluation_cases(EvaluationSplit.DEVELOPMENT, root=root)
    holdout = load_agent_evaluation_cases(EvaluationSplit.HOLDOUT, root=root)
    if {case.id for case in development} & {case.id for case in holdout}:
        raise EvaluationDataError("Agent development and holdout case IDs must be disjoint.")


def agent_dataset_fingerprint(cases: tuple[AgentEvaluationCase, ...]) -> str:
    canonical = json.dumps(
        [case.model_dump(mode="json") for case in cases],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
