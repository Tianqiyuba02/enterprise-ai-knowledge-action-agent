import inspect
from pathlib import Path

from app.workflow.domain import WorkflowState
from app.workflow.orchestration import WorkflowOrchestrationService
from app.workflow.worker import (
    WorkflowWorker,
    confirmation_event_settled,
    reconciliation_event_settled,
)
from app.workflow.worker_cli import run

ROOT = Path(__file__).resolve().parents[2]


def test_worker_entry_point_and_internal_resume_have_no_thread_id_parameter() -> None:
    assert run.__module__ == "app.workflow.worker_cli"
    assert "thread_id" not in inspect.signature(WorkflowWorker.deliver).parameters
    assert (
        "thread_id"
        not in inspect.signature(WorkflowOrchestrationService.resume_internal).parameters
    )
    assert (
        "owner_subject_id"
        not in inspect.signature(WorkflowOrchestrationService.resume_internal).parameters
    )


def test_worker_source_has_no_provider_or_execution_path() -> None:
    combined = "\n".join(
        (ROOT / "src" / "app" / "workflow" / name).read_text(encoding="utf-8")
        for name in ("worker.py", "worker_cli.py")
    ).lower()
    assert "gemini" not in combined
    assert "google.genai" not in combined


def test_confirmation_and_reconciliation_settlement_are_event_type_specific() -> None:
    assert confirmation_event_settled(WorkflowState.UNKNOWN_OUTCOME.value) is True
    assert confirmation_event_settled(WorkflowState.SUCCEEDED.value) is True
    assert confirmation_event_settled(WorkflowState.RECONCILING.value) is False
    assert (
        reconciliation_event_settled(
            WorkflowState.UNKNOWN_OUTCOME.value, manual_review_required=False
        )
        is False
    )
    assert (
        reconciliation_event_settled(WorkflowState.RECONCILING.value, manual_review_required=False)
        is False
    )
    assert (
        reconciliation_event_settled(
            WorkflowState.UNKNOWN_OUTCOME.value, manual_review_required=True
        )
        is True
    )
    assert (
        reconciliation_event_settled(WorkflowState.SUCCEEDED.value, manual_review_required=False)
        is True
    )
