import json
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.api.knowledge_models import KnowledgeCitation, KnowledgeQueryResponse
from app.embeddings.client import EmbeddingRateLimitError
from app.evaluation.cli import main as evaluation_main
from app.evaluation.loader import (
    EvaluationDataError,
    evaluation_dataset_fingerprint,
    load_evaluation_cases,
    validate_all_splits,
)
from app.evaluation.metrics import (
    build_summary,
    evaluate_retrieval_case,
    evaluate_semantic_case,
)
from app.evaluation.models import (
    CaseAttempt,
    CaseExecutionState,
    DocumentIdentity,
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationConfiguration,
    EvaluationMode,
    EvaluationReport,
    EvaluationSplit,
    ResultOrigin,
)
from app.evaluation.runner import EvaluationRunner, ResumeCompatibilityError
from app.grounding.client import GroundedRateLimitError
from app.knowledge.context import KnowledgeApplicabilityContext
from app.knowledge.models import RetrievedEvidence
from app.knowledge.vocabulary import AudienceGroup, Jurisdiction

TODAY = date(2026, 8, 25)


def _identity(doc_code: str, version: str = "1.0") -> DocumentIdentity:
    return DocumentIdentity(doc_code=doc_code, version=version)


def _case(**overrides: object) -> EvaluationCase:
    values: dict[str, object] = {
        "id": "dev_test_case",
        "split": "development",
        "question": "What is the current policy?",
        "expected_status": "answered",
        "required_documents": [{"doc_code": "POL-HR-001", "version": "2.0"}],
        "allowed_documents": [{"doc_code": "POL-HR-001", "version": "2.0"}],
        "forbidden_documents": [{"doc_code": "POL-HR-001", "version": "1.0"}],
        "expected_section_anchors": ["entitlement"],
        "rationale": "Synthetic metric test.",
    }
    values.update(overrides)
    return EvaluationCase.model_validate(values)


def _context() -> KnowledgeApplicabilityContext:
    return KnowledgeApplicabilityContext(
        jurisdiction=Jurisdiction.AU_VIC,
        audience_groups=frozenset(
            {
                AudienceGroup.ALL_EMPLOYEES,
                AudienceGroup.MELBOURNE_EMPLOYEES,
            }
        ),
    )


def _evidence(
    doc_code: str,
    version: str,
    *,
    jurisdiction: Jurisdiction = Jurisdiction.AU_VIC,
    token_count: int = 20,
    distance: float = 0.2,
) -> RetrievedEvidence:
    return RetrievedEvidence(
        document_id=uuid.uuid4(),
        chunk_id=uuid.uuid4(),
        doc_code=doc_code,
        version=version,
        title=f"Synthetic {doc_code}",
        status="approved",
        effective_date=date(2026, 1, 1),
        expiry_date=None,
        jurisdiction=jurisdiction,
        audience_groups=frozenset({AudienceGroup.ALL_EMPLOYEES}),
        section_label="Entitlement",
        anchor="entitlement",
        page=None,
        content=f"Evidence for {doc_code}.",
        token_count=token_count,
        cosine_distance=distance,
    )


def _configuration() -> EvaluationConfiguration:
    return EvaluationConfiguration(
        embedding_model="gemini-embedding-2",
        embedding_dimension=768,
        grounded_generation_model="gemini-3.6-flash",
        retrieval_metric="exact_pgvector_cosine_distance",
        top_k=6,
        minimum_similarity_threshold=None,
        chunk_target_tokens=400,
        chunk_overlap_tokens=50,
        trusted_as_of_date=TODAY,
        corpus_documents=12,
        corpus_chunks=42,
    )


def test_version_controlled_splits_are_strict_disjoint_and_expected_size() -> None:
    validate_all_splits()
    development = load_evaluation_cases(EvaluationSplit.DEVELOPMENT)
    holdout = load_evaluation_cases(EvaluationSplit.HOLDOUT)

    assert len(development) == 20
    assert len(holdout) == 8
    assert {case.id for case in development}.isdisjoint({case.id for case in holdout})
    assert all(case.split is EvaluationSplit.DEVELOPMENT for case in development)
    assert all(case.split is EvaluationSplit.HOLDOUT for case in holdout)


@pytest.mark.parametrize(
    "payload",
    [
        {"extra": "forbidden"},
        {
            "expected_status": "answered",
            "required_documents": [],
            "allowed_documents": [],
        },
        {
            "expected_status": "conflicting_evidence",
            "required_documents": [{"doc_code": "POL-HR-001", "version": "2.0"}],
        },
    ],
)
def test_evaluation_case_schema_rejects_invalid_labels(payload: dict[str, object]) -> None:
    base = _case().model_dump(mode="json")
    base.update(payload)

    with pytest.raises(ValidationError):
        EvaluationCase.model_validate(base)


def test_insufficient_case_can_require_retrieval_relevance_without_citations() -> None:
    case = _case(
        expected_status="insufficient_evidence",
        required_documents=[
            {"doc_code": "POL-HR-001", "version": "2.0"},
            {"doc_code": "POL-HR-002", "version": "1.0"},
        ],
        allowed_documents=[
            {"doc_code": "POL-HR-001", "version": "2.0"},
            {"doc_code": "POL-HR-002", "version": "1.0"},
        ],
    )
    evidence = (
        _evidence("POL-HR-001", "2.0"),
        _evidence("POL-HR-002", "1.0"),
    )
    retrieval_metrics = evaluate_retrieval_case(
        case,
        evidence,
        applicability=_context(),
        trusted_today=TODAY,
    )
    response = KnowledgeQueryResponse(
        status="insufficient_evidence",
        answer="The evidence does not define annual-leave purpose, so I will not guess.",
        citations=(),
    )
    semantic_metrics = evaluate_semantic_case(case, response)

    assert retrieval_metrics.required_document_recall_at_k == 1.0
    assert semantic_metrics.status_correct is True
    assert semantic_metrics.citation_presence_valid is True
    assert semantic_metrics.required_document_citation_recall is None


def test_loader_rejects_duplicate_ids(tmp_path: Path) -> None:
    development = tmp_path / "development"
    holdout = tmp_path / "holdout"
    development.mkdir()
    holdout.mkdir()
    line = _case().model_dump_json()
    (development / "rag_cases.jsonl").write_text(f"{line}\n{line}\n", encoding="utf-8")
    holdout_case = _case(id="holdout_test", split="holdout")
    (holdout / "rag_cases.jsonl").write_text(
        holdout_case.model_dump_json() + "\n",
        encoding="utf-8",
    )

    with pytest.raises(EvaluationDataError, match="duplicate"):
        load_evaluation_cases(EvaluationSplit.DEVELOPMENT, root=tmp_path)


def test_retrieval_metrics_measure_recall_rank_forbidden_authority_and_tiny_chunks() -> None:
    case = _case(
        forbidden_documents=[{"doc_code": "POL-WRK-002", "version": "1.0"}],
    )
    evidence = (
        _evidence(
            "POL-WRK-002",
            "1.0",
            jurisdiction=Jurisdiction.AU_NSW,
            token_count=4,
            distance=0.0,
        ),
        _evidence("POL-HR-001", "2.0", token_count=30, distance=0.2),
    )

    metrics = evaluate_retrieval_case(
        case,
        evidence,
        applicability=_context(),
        trusted_today=TODAY,
    )

    assert metrics.required_document_recall_at_k == 1.0
    assert metrics.first_relevant_rank == 2
    assert metrics.reciprocal_rank == 0.5
    assert metrics.forbidden_document_hits == 1
    assert metrics.authority_violations == 1
    assert metrics.returned_chunks == 2
    assert metrics.document_diversity == 2
    assert metrics.tiny_chunks == 1
    assert metrics.tiny_chunk_rate == 0.5


def test_semantic_metrics_measure_status_citations_and_internal_ref_leakage() -> None:
    case = _case()
    response = KnowledgeQueryResponse(
        status="answered",
        answer="According to E1, eligible employees receive twenty days.",
        citations=(
            KnowledgeCitation(
                doc_code="POL-HR-001",
                title="Annual Leave Policy",
                version="2.0",
                section_anchor="entitlement",
                page=None,
            ),
        ),
    )

    metrics = evaluate_semantic_case(case, response)

    assert metrics.status_correct is True
    assert metrics.citation_presence_valid is True
    assert metrics.required_document_citation_recall == 1.0
    assert metrics.allowed_document_citations_valid is True
    assert metrics.forbidden_citation_hits == 0
    assert metrics.public_citation_metadata_valid is True
    assert metrics.internal_reference_leaked is True


def test_conflict_source_metric_requires_two_distinct_documents() -> None:
    case = _case(
        expected_status="conflicting_evidence",
        required_documents=[
            {"doc_code": "POL-SEC-004", "version": "1.0"},
            {"doc_code": "SOP-FAC-007", "version": "1.0"},
        ],
        allowed_documents=[
            {"doc_code": "POL-SEC-004", "version": "1.0"},
            {"doc_code": "SOP-FAC-007", "version": "1.0"},
        ],
        forbidden_documents=[],
    )
    response = KnowledgeQueryResponse(
        status="conflicting_evidence",
        answer="Two approved sources conflict.",
        citations=(
            KnowledgeCitation(
                doc_code="POL-SEC-004",
                title="Access Policy",
                version="1.0",
                section_anchor="access-window",
            ),
            KnowledgeCitation(
                doc_code="SOP-FAC-007",
                title="Facilities Guide",
                version="1.0",
                section_anchor="escort-requirement",
            ),
        ),
    )

    metrics = evaluate_semantic_case(case, response)

    assert metrics.status_correct is True
    assert metrics.conflict_distinct_sources_valid is True
    assert metrics.required_document_citation_recall == 1.0


class RateLimitedRetrieval:
    def retrieve(self, _question, _applicability):
        raise EmbeddingRateLimitError("safe rate limit")


class UnusedGrounded:
    def query(self, _question, _applicability):
        raise AssertionError("grounded path must not run")


def test_runner_stops_and_reports_provider_rate_limit_safely() -> None:
    cases = (_case(), _case(id="dev_second_case"))
    runner = EvaluationRunner(
        retrieval=RateLimitedRetrieval(),
        grounded=UnusedGrounded(),
        applicability=_context(),
        trusted_today=TODAY,
        configuration=_configuration(),
    )

    report = runner.run(
        mode=EvaluationMode.RETRIEVAL,
        split=EvaluationSplit.DEVELOPMENT,
        cases=cases,
        dataset_fingerprint=evaluation_dataset_fingerprint(cases),
    )

    assert report.summary.cases_completed == 0
    assert report.summary.cases_blocked_by_provider_rate_limit == 1
    assert report.summary.cases_not_run == 1
    assert report.cases[0].state is CaseExecutionState.BLOCKED_BY_PROVIDER_RATE_LIMIT
    assert report.cases[0].safe_error_category == "EmbeddingRateLimitError"


def test_report_serialization_contains_frozen_configuration_and_no_internal_ids() -> None:
    result = EvaluationCaseResult(
        case_id="dev_test_case",
        state="completed",
    )
    report = EvaluationReport(
        generated_at=datetime.now(UTC),
        mode="retrieval",
        split="development",
        dataset_fingerprint="a" * 64,
        configuration=_configuration(),
        summary=build_summary(
            cases_total=1,
            mode=EvaluationMode.RETRIEVAL,
            results=(result,),
        ),
        cases=(result,),
    )

    serialized = report.model_dump_json()

    assert json.loads(serialized)["configuration"]["top_k"] == 6
    assert json.loads(serialized)["configuration"]["minimum_similarity_threshold"] is None
    assert "document_id" not in serialized
    assert "chunk_id" not in serialized
    assert report.no_tuning_performed is True


def test_cli_requires_explicit_live_mode(capsys) -> None:
    exit_code = evaluation_main(["--mode", "retrieval", "--split", "development"])

    assert exit_code == 2
    assert "explicit --live" in capsys.readouterr().err


class UnusedRetrieval:
    def retrieve(self, _question, _applicability):
        raise AssertionError("retrieval-only path must not run")


class SequenceGrounded:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.calls: list[str] = []

    def query(self, question, _applicability):
        self.calls.append(question)
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _answered_response() -> KnowledgeQueryResponse:
    return KnowledgeQueryResponse(
        status="answered",
        answer="Eligible employees receive twenty days.",
        citations=(
            KnowledgeCitation(
                doc_code="POL-HR-001",
                title="Annual Leave Policy",
                version="2.0",
                section_anchor="entitlement",
            ),
        ),
    )


def _completed_result(case: EvaluationCase) -> EvaluationCaseResult:
    response = _answered_response()
    return EvaluationCaseResult(
        case_id=case.id,
        state=CaseExecutionState.COMPLETED,
        semantic_status=response.status,
        answer=response.answer,
        citations=tuple(
            {
                "doc_code": citation.doc_code,
                "version": citation.version,
                "section_anchor": citation.section_anchor,
                "page": citation.page,
            }
            for citation in response.citations
        ),
        semantic_metrics=evaluate_semantic_case(case, response),
    )


def _previous_report(
    cases: tuple[EvaluationCase, ...],
    results: tuple[EvaluationCaseResult, ...],
    *,
    configuration: EvaluationConfiguration | None = None,
    fingerprint: str | None = None,
) -> EvaluationReport:
    resolved_fingerprint = fingerprint or evaluation_dataset_fingerprint(cases)
    return EvaluationReport(
        generated_at=datetime.now(UTC),
        mode=EvaluationMode.GROUNDED,
        split=EvaluationSplit.DEVELOPMENT,
        dataset_fingerprint=resolved_fingerprint,
        configuration=configuration or _configuration(),
        summary=build_summary(
            cases_total=len(cases),
            mode=EvaluationMode.GROUNDED,
            results=results,
        ),
        cases=results,
    )


def test_resume_carries_completed_retries_blocked_and_continues_unattempted() -> None:
    cases = (
        _case(id="dev_resume_one", question="Question one"),
        _case(id="dev_resume_two", question="Question two"),
        _case(id="dev_resume_three", question="Question three"),
    )
    previous = _previous_report(
        cases,
        (
            _completed_result(cases[0]),
            EvaluationCaseResult(
                case_id=cases[1].id,
                state=CaseExecutionState.BLOCKED_BY_PROVIDER_RATE_LIMIT,
                safe_error_category="GroundedRateLimitError",
            ),
        ),
    )
    grounded = SequenceGrounded([_answered_response(), _answered_response()])
    runner = EvaluationRunner(
        retrieval=UnusedRetrieval(),
        grounded=grounded,
        applicability=_context(),
        trusted_today=TODAY,
        configuration=_configuration(),
    )

    report = runner.run(
        mode=EvaluationMode.GROUNDED,
        split=EvaluationSplit.DEVELOPMENT,
        cases=cases,
        dataset_fingerprint=evaluation_dataset_fingerprint(cases),
        previous_report=previous,
    )

    assert grounded.calls == ["Question two", "Question three"]
    assert [result.case_id for result in report.cases] == [case.id for case in cases]
    assert len({result.case_id for result in report.cases}) == 3
    assert report.cases[0].result_origin is ResultOrigin.CARRIED_FORWARD
    assert report.cases[1].result_origin is ResultOrigin.CURRENT_INVOCATION
    assert [attempt.state for attempt in report.cases[1].attempt_history] == [
        CaseExecutionState.BLOCKED_BY_PROVIDER_RATE_LIMIT,
        CaseExecutionState.COMPLETED,
    ]
    assert report.summary.cases_completed == 3
    assert report.summary.cases_carried_forward == 1
    assert report.summary.cases_completed_current_invocation == 2
    assert report.summary.semantic_status_accuracy == 1.0


def test_resume_rate_limit_retries_once_then_stops_without_next_case() -> None:
    cases = (
        _case(id="dev_rate_one", question="Question one"),
        _case(id="dev_rate_two", question="Question two"),
        _case(id="dev_rate_three", question="Question three"),
    )
    previous = _previous_report(
        cases,
        (
            _completed_result(cases[0]),
            EvaluationCaseResult(
                case_id=cases[1].id,
                state=CaseExecutionState.BLOCKED_BY_PROVIDER_RATE_LIMIT,
                attempt_history=(
                    CaseAttempt(
                        state=CaseExecutionState.BLOCKED_BY_PROVIDER_RATE_LIMIT,
                        safe_error_category="GroundedRateLimitError",
                    ),
                ),
                safe_error_category="GroundedRateLimitError",
            ),
        ),
    )
    grounded = SequenceGrounded([GroundedRateLimitError("safe rate limit")])
    runner = EvaluationRunner(
        retrieval=UnusedRetrieval(),
        grounded=grounded,
        applicability=_context(),
        trusted_today=TODAY,
        configuration=_configuration(),
    )

    report = runner.run(
        mode=EvaluationMode.GROUNDED,
        split=EvaluationSplit.DEVELOPMENT,
        cases=cases,
        dataset_fingerprint=evaluation_dataset_fingerprint(cases),
        previous_report=previous,
    )

    assert grounded.calls == ["Question two"]
    assert [result.case_id for result in report.cases] == [
        "dev_rate_one",
        "dev_rate_two",
    ]
    assert [attempt.state for attempt in report.cases[1].attempt_history] == [
        CaseExecutionState.BLOCKED_BY_PROVIDER_RATE_LIMIT,
        CaseExecutionState.BLOCKED_BY_PROVIDER_RATE_LIMIT,
    ]
    assert report.summary.cases_completed == 1
    assert report.summary.cases_not_run == 1


@pytest.mark.parametrize("mismatch", ["configuration", "fingerprint", "split", "mode"])
def test_resume_rejects_incompatible_reports(mismatch: str) -> None:
    cases = (_case(id="dev_compatibility"),)
    previous = _previous_report(cases, (_completed_result(cases[0]),))
    mode = EvaluationMode.GROUNDED
    split = EvaluationSplit.DEVELOPMENT
    fingerprint = evaluation_dataset_fingerprint(cases)
    if mismatch == "configuration":
        changed = _configuration().model_copy(update={"top_k": 5})
        previous = previous.model_copy(update={"configuration": changed})
    elif mismatch == "fingerprint":
        fingerprint = "f" * 64
    elif mismatch == "split":
        split = EvaluationSplit.HOLDOUT
    else:
        mode = EvaluationMode.RETRIEVAL
    runner = EvaluationRunner(
        retrieval=UnusedRetrieval(),
        grounded=SequenceGrounded([]),
        applicability=_context(),
        trusted_today=TODAY,
        configuration=_configuration(),
    )

    with pytest.raises(ResumeCompatibilityError):
        runner.run(
            mode=mode,
            split=split,
            cases=cases,
            dataset_fingerprint=fingerprint,
            previous_report=previous,
        )


def test_evaluator_delay_applies_only_between_current_attempts() -> None:
    cases = (
        _case(id="dev_delay_one", question="Question one"),
        _case(id="dev_delay_two", question="Question two"),
        _case(id="dev_delay_three", question="Question three"),
    )
    delays: list[float] = []
    runner = EvaluationRunner(
        retrieval=UnusedRetrieval(),
        grounded=SequenceGrounded(
            [_answered_response(), _answered_response(), _answered_response()]
        ),
        applicability=_context(),
        trusted_today=TODAY,
        configuration=_configuration(),
        sleep=delays.append,
    )

    report = runner.run(
        mode=EvaluationMode.GROUNDED,
        split=EvaluationSplit.DEVELOPMENT,
        cases=cases,
        dataset_fingerprint=evaluation_dataset_fingerprint(cases),
        delay_seconds=2.5,
    )

    assert report.summary.cases_completed == 3
    assert delays == [2.5, 2.5]


def test_cli_resume_requires_existing_report_without_live_calls(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "missing.json"

    exit_code = evaluation_main(
        [
            "--mode",
            "grounded",
            "--split",
            "development",
            "--live",
            "--resume",
            "--output",
            str(missing),
        ]
    )

    assert exit_code == 2
    assert "--resume requires an existing report" in capsys.readouterr().err
