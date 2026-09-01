"""Read-only loader for closed V4 development Run 1. Never rewrite it as Run 2."""

import json
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.evaluation.v4.transport import (
    RUN1_ARCHIVE_PATH,
    RUN1_EVALUATOR_VERSION,
    RUN1_EVIDENCE_COMMIT,
    RUN1_IDENTITY_PATH,
    RUN1_LIVE_PATH,
    RUN1_PROVIDER_BLOCKED,
    RUN1_PROVIDER_COMPLETED,
    RUN1_SEMANTIC_PASS_AMONG_EVALUABLE,
    RUN1_STATUS,
)

PROTECTED_RUN1_PATHS: Final = (
    Path(RUN1_LIVE_PATH),
    Path(RUN1_ARCHIVE_PATH),
    Path(RUN1_IDENTITY_PATH),
)


class ClosedRun1Identity(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    run: int
    dataset_kind: str
    evaluator_version: str
    development_set_version: str
    status: str
    not_a_pass: bool
    not_a_holdout: bool
    evidence_commit: str
    artifact: str
    provider_completed_count: int
    provider_blocked_count: int
    semantic_evaluable_count: int
    semantic_pass_count: int
    unobserved_families: tuple[str, ...]
    rate_limited_means: str


class ClosedRun1Summary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider_completed_count: int
    provider_blocked_count: int
    semantic_evaluable_count: int
    case_semantic_pass_rate: float | None
    safety_gate_failed: bool


class ClosedRun1Report(BaseModel):
    """Permissive reader for the frozen eval-1 artifact. Not an eval-2 report."""

    model_config = ConfigDict(extra="ignore")

    evaluator_version: str
    development_set_version: str
    report_version: str
    commit: str
    dataset_fingerprint: str
    summary: ClosedRun1Summary
    cases: tuple[dict[str, Any], ...] = Field(min_length=16, max_length=16)


class ClosedRun1Evidence:
    def __init__(self, report: ClosedRun1Report, identity: ClosedRun1Identity) -> None:
        self.report = report
        self.identity = identity


def is_closed_run1_report(payload: dict[str, Any]) -> bool:
    return payload.get("evaluator_version") == RUN1_EVALUATOR_VERSION


def load_closed_run1_report(path: Path | None = None) -> ClosedRun1Report:
    target = path or Path(RUN1_ARCHIVE_PATH)
    raw = json.loads(target.read_text(encoding="utf-8"))
    if not is_closed_run1_report(raw):
        raise ValueError("artifact is not closed V4 development Run 1 evidence")
    try:
        report = ClosedRun1Report.model_validate(raw)
    except ValidationError as exc:
        raise ValueError("closed Run 1 artifact is unreadable") from exc
    if report.summary.provider_completed_count != RUN1_PROVIDER_COMPLETED:
        raise ValueError("closed Run 1 completed count does not match archived identity")
    if report.summary.provider_blocked_count != RUN1_PROVIDER_BLOCKED:
        raise ValueError("closed Run 1 blocked count does not match archived identity")
    return report


def load_closed_run1_identity(path: Path | None = None) -> ClosedRun1Identity:
    target = path or Path(RUN1_IDENTITY_PATH)
    identity = ClosedRun1Identity.model_validate_json(target.read_text(encoding="utf-8"))
    if identity.status != RUN1_STATUS:
        raise ValueError("closed Run 1 status must remain PARTIAL / PROVIDER-LIMITED")
    if identity.evidence_commit != RUN1_EVIDENCE_COMMIT:
        raise ValueError("closed Run 1 evidence commit does not match")
    if identity.semantic_pass_count != identity.semantic_evaluable_count:
        raise ValueError("closed Run 1 semantic pass identity is inconsistent")
    if f"{identity.semantic_pass_count}/{identity.semantic_evaluable_count}" != (
        RUN1_SEMANTIC_PASS_AMONG_EVALUABLE
    ):
        raise ValueError("closed Run 1 semantic identity does not match")
    return identity


def load_closed_run1_evidence() -> ClosedRun1Evidence:
    return ClosedRun1Evidence(load_closed_run1_report(), load_closed_run1_identity())


def refuse_eval2_write_over_run1(output: Path, existing_text: str | None = None) -> None:
    resolved = output.resolve()
    protected = {path.resolve() for path in PROTECTED_RUN1_PATHS if path.exists()}
    if resolved in protected:
        raise ValueError("refusing to overwrite closed V4 development Run 1 evidence")
    if existing_text is None:
        return
    try:
        payload = json.loads(existing_text)
    except json.JSONDecodeError:
        return
    if is_closed_run1_report(payload):
        raise ValueError("refusing to rewrite Run 1 evidence as an eval-2 report")
