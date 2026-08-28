import os
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

from app.agent.leave_models import LeavePreparationStatus, LeaveRequestDraft
from app.agent.loop_models import AgentRunResult, AgentRunStatus
from app.api.dependencies import DEMO_IDENTITY_BINDINGS
from app.config import load_knowledge_settings
from app.evaluation.v4.clock import V4_DEVELOPMENT_BUSINESS_DATE
from app.evaluation.v4.fingerprints import baseline_data_fingerprint, build_fingerprints
from app.evaluation.v4.isolation import (
    EXPECTED_CHUNKS,
    EXPECTED_DOCUMENTS,
    EXPECTED_HOLIDAYS,
    isolated_evaluation_database,
    workflow_counts,
)
from app.evaluation.v4.loader import load_v4_development_cases, v4_dataset_fingerprint
from app.evaluation.v4.metrics import build_summary
from app.evaluation.v4.models import (
    V4CaseExecutionState,
    V4EvaluationConfiguration,
    V4ProductEvaluationReport,
)
from app.evaluation.v4.runner import V4ProductEvaluationRunner, V4ResumeCompatibilityError

POSTGRES_ENABLED = os.getenv("RUN_POSTGRES_TESTS") == "1"

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not POSTGRES_ENABLED,
        reason="set RUN_POSTGRES_TESTS=1 with the V2 PostgreSQL container running",
    ),
]

ALEX = DEMO_IDENTITY_BINDINGS["demo-v1-7f4c2a91"]


class ScriptedAgent:
    def __init__(self, results: list[AgentRunResult]) -> None:
        self._results = list(results)

    def run(self, message: str, context) -> AgentRunResult:
        if not self._results:
            raise AssertionError("unexpected extra AgentService call")
        return self._results.pop(0)


def _draft(
    start: date,
    end: date | None = None,
    *,
    reason: str = "appointment",
    hours: str = "7.60",
    days: int = 1,
) -> LeaveRequestDraft:
    return LeaveRequestDraft(
        leave_type="annual",
        start_date=start,
        end_date=end or start,
        scheduled_work_days=days,
        requested_hours=Decimal(hours),
        current_balance_hours=Decimal("76.00"),
        projected_balance_hours=Decimal("68.40"),
        preparation_status=LeavePreparationStatus.READY,
        reason=reason,
        public_holiday_check_required=True,
        non_executing=True,
    )


def _completed(draft: LeaveRequestDraft | None, answer: str = "Prepared.") -> AgentRunResult:
    return AgentRunResult(
        status=AgentRunStatus.COMPLETED,
        answer=answer,
        citations=(),
        prepared_leave_request=draft,
        tool_calls_attempted=1 if draft is not None else 0,
        model_rounds=1,
    )


def _runner(isolated, agent) -> V4ProductEvaluationRunner:
    cases = load_v4_development_cases()
    fingerprint = v4_dataset_fingerprint(cases)
    fingerprints = build_fingerprints(
        fingerprint,
        baseline_data=isolated.baseline_data_fingerprint,
    )
    configuration = V4EvaluationConfiguration(
        agent_model="scripted",
        agent_timeout_seconds=60,
        agent_max_attempts=1,
        trusted_evaluation_date=V4_DEVELOPMENT_BUSINESS_DATE,
        corpus_documents=EXPECTED_DOCUMENTS,
        corpus_chunks=EXPECTED_CHUNKS,
        holiday_rows=EXPECTED_HOLIDAYS,
        calendar_version="AU-VIC-2026-v1",
        fingerprints=fingerprints,
    )
    return V4ProductEvaluationRunner(
        agent=agent,
        session_factory=isolated.session_factory,
        settings=isolated.settings,
        engine=isolated.engine,
        configuration=configuration,
        fingerprints=fingerprints,
        branch="feature/v4-workflow-foundation",
        commit="b" * 40,
    )


def test_isolated_database_copies_corpus_without_touching_source() -> None:
    with isolated_evaluation_database() as isolated:
        counts = workflow_counts(isolated.engine)
        assert counts["action_workflows"] == 0
        with isolated.engine.connect() as connection:
            documents = connection.execute(text("SELECT count(*) FROM documents")).scalar_one()
            chunks = connection.execute(text("SELECT count(*) FROM document_chunks")).scalar_one()
            holidays = connection.execute(text("SELECT count(*) FROM public_holidays")).scalar_one()
        assert documents == EXPECTED_DOCUMENTS
        assert chunks == EXPECTED_CHUNKS
        assert holidays == EXPECTED_HOLIDAYS
        first = isolated.baseline_data_fingerprint
        second = baseline_data_fingerprint(isolated.engine)
        assert first == second
        with isolated.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE documents
                    SET title = title || ' changed'
                    WHERE id = (SELECT id FROM documents ORDER BY ingested_at LIMIT 1)
                    """
                )
            )
        changed = baseline_data_fingerprint(isolated.engine)
        assert changed != first
        with isolated.engine.connect() as recount:
            assert recount.execute(text("SELECT count(*) FROM documents")).scalar_one() == (
                EXPECTED_DOCUMENTS
            )
            assert recount.execute(text("SELECT count(*) FROM document_chunks")).scalar_one() == (
                EXPECTED_CHUNKS
            )
    source = create_engine(load_knowledge_settings().database_url.get_secret_value())
    try:
        with source.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM documents")).scalar_one() == 12
    finally:
        source.dispose()


def test_scripted_created_reused_not_created_and_lifecycle() -> None:
    cases = {case.id: case for case in load_v4_development_cases()}
    selected = (
        cases["dev_v4_a1_executable_single"],
        cases["dev_v4_b1_afl_holiday"],
        cases["dev_v4_e1_repeat_prepare_reuse"],
        cases["dev_v4_e3_unknown_blocks_replace"],
        cases["dev_v4_e4_succeeded_no_duplicate"],
    )
    agent = ScriptedAgent(
        [
            _completed(_draft(date(2026, 9, 14))),
            _completed(_draft(date(2026, 9, 25))),
            _completed(_draft(date(2026, 11, 16))),
            _completed(_draft(date(2026, 11, 16))),
            _completed(_draft(date(2026, 11, 18))),
            _completed(_draft(date(2026, 11, 19))),
        ]
    )
    with isolated_evaluation_database() as isolated:
        report = _runner(isolated, agent).run(
            cases=selected,
            dataset_fingerprint=v4_dataset_fingerprint(load_v4_development_cases()),
        )
        by_id = {item.case_id: item for item in report.cases}
        assert by_id["dev_v4_a1_executable_single"].product.action_status == "created"
        assert by_id["dev_v4_b1_afl_holiday"].product.action_status == "not_created"
        assert by_id["dev_v4_b1_afl_holiday"].product.action_id is None
        assert by_id["dev_v4_e1_repeat_prepare_reuse"].product.action_status == "reused"
        assert by_id["dev_v4_e3_unknown_blocks_replace"].product.action_state == "UNKNOWN_OUTCOME"
        assert by_id["dev_v4_e4_succeeded_no_duplicate"].product.action_state == "SUCCEEDED"
        assert by_id["dev_v4_e4_succeeded_no_duplicate"].business.leave_request_count == 0
        assert report.summary.safety_gate_failed is False
        assert workflow_counts(isolated.engine)["action_workflows"] == 0


def test_scripted_f1_does_not_persist_confirmation_token() -> None:
    case = next(item for item in load_v4_development_cases() if item.id == "dev_v4_f1_full_e2e")
    october = _completed(_draft(date(2026, 10, 21)))
    agent = ScriptedAgent([october, _completed(_draft(date(2026, 10, 21)))])
    with isolated_evaluation_database() as isolated:
        report = _runner(isolated, agent).run(
            cases=(case,),
            dataset_fingerprint=v4_dataset_fingerprint(load_v4_development_cases()),
        )
        result = report.cases[0]
        assert result.state is V4CaseExecutionState.COMPLETED
        assert result.business is not None
        assert result.business.leave_request_count == 1
        assert result.business.final_state == "SUCCEEDED"
        dumped = report.model_dump_json()
        assert "confirmation_token" not in dumped
        assert ALEX.session_id not in dumped
        assert result.model is not None
        assert result.model.tool_trace_available is False
        assert result.model.tool_names is None


def test_resume_refuses_same_count_corpus_content_change() -> None:
    cases = load_v4_development_cases()
    gold = v4_dataset_fingerprint(cases)
    with isolated_evaluation_database() as isolated:
        matching = build_fingerprints(gold, baseline_data=isolated.baseline_data_fingerprint)
        with isolated.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE document_chunks
                    SET content = content || ' changed'
                    WHERE id = (SELECT id FROM document_chunks ORDER BY created_at LIMIT 1)
                    """
                )
            )
        changed_baseline = baseline_data_fingerprint(isolated.engine)
        assert changed_baseline != isolated.baseline_data_fingerprint
        configuration = V4EvaluationConfiguration(
            agent_model="scripted",
            agent_timeout_seconds=60,
            agent_max_attempts=1,
            trusted_evaluation_date=V4_DEVELOPMENT_BUSINESS_DATE,
            corpus_documents=EXPECTED_DOCUMENTS,
            corpus_chunks=EXPECTED_CHUNKS,
            holiday_rows=EXPECTED_HOLIDAYS,
            calendar_version="AU-VIC-2026-v1",
            fingerprints=matching,
        )
        previous = V4ProductEvaluationReport(
            generated_at=datetime.now(UTC),
            branch="feature/v4-workflow-foundation",
            commit="b" * 40,
            dataset_fingerprint=gold,
            configuration=configuration,
            summary=build_summary((), cases),
            cases=(),
        )
        runner = V4ProductEvaluationRunner.__new__(V4ProductEvaluationRunner)
        runner._configuration = configuration
        runner._fingerprints = matching.model_copy(update={"baseline_data": changed_baseline})
        with pytest.raises(V4ResumeCompatibilityError, match="baseline-data"):
            runner._validate_resume(previous, cases=cases, dataset_fingerprint=gold)
