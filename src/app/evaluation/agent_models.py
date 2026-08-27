"""Strict mechanical contracts for the V3 agent evaluation baseline."""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.agent.contracts import V3ToolName
from app.agent.leave_models import LeavePreparationStatus
from app.agent.models import ToolResultStatus
from app.agent.provider_failures import AgentProviderFailureDetail
from app.evaluation.models import (
    CaseId,
    DatasetFingerprint,
    DocumentIdentity,
    EvaluationSplit,
    ResultOrigin,
)

NonEmptyString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, strict=True),
]
UserMessage = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4_000, strict=True),
]
Sha256Fingerprint = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{64}$", strict=True),
]
SafeArgumentValue = str | int | float | bool | None


class AgentEvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentCaseCategory(StrEnum):
    SIMPLE_READ = "simple_read"
    KNOWLEDGE = "knowledge"
    MULTI_TOOL = "multi_tool"
    PREPARE = "prepare"
    AUTHORIZATION_SAFETY = "authorization_safety"
    NO_TOOL = "no_tool"
    EXECUTION_BOUNDARY = "execution_boundary"
    PROMPT_INJECTION = "prompt_injection"
    CONVERSATIONAL_CONFIRMATION = "conversational_confirmation"


class EmployeeFixture(StrEnum):
    ALEX = "alex"
    SAM = "sam"


class ExpectedAssistantStatus(StrEnum):
    COMPLETED = "completed"
    UNABLE_TO_COMPLETE = "unable_to_complete"


class AgentCaseExecutionState(StrEnum):
    COMPLETED = "completed"
    PROVIDER_BLOCKED = "provider_blocked"
    ERROR = "error"


class ForbiddenToolCall(AgentEvaluationModel):
    tool: V3ToolName
    arguments: dict[str, SafeArgumentValue] = Field(default_factory=dict)


class ExpectedToolOutcome(AgentEvaluationModel):
    tool: V3ToolName
    status: ToolResultStatus


class ExpectedPreparedAction(AgentEvaluationModel):
    leave_type: Literal["annual"]
    start_date: date
    end_date: date
    scheduled_work_days: Annotated[int, Field(ge=0)]
    requested_hours: Decimal
    current_balance_hours: Decimal
    projected_balance_hours: Decimal
    preparation_status: LeavePreparationStatus
    non_executing: Literal[True]


class AgentEvaluationCase(AgentEvaluationModel):
    id: CaseId
    split: EvaluationSplit
    category: AgentCaseCategory
    employee_fixture: EmployeeFixture
    user_message: UserMessage
    expected_public_status: ExpectedAssistantStatus
    required_tools: tuple[V3ToolName, ...] = ()
    allowed_tools: tuple[V3ToolName, ...] = ()
    forbidden_tools: tuple[V3ToolName, ...] = ()
    forbidden_calls: tuple[ForbiddenToolCall, ...] = ()
    expected_tool_outcomes: tuple[ExpectedToolOutcome, ...] = ()
    expected_citation_documents: tuple[DocumentIdentity, ...] = ()
    forbidden_citation_documents: tuple[DocumentIdentity, ...] = ()
    expected_prepared_action: ExpectedPreparedAction | None = None
    forbidden_output_terms: tuple[NonEmptyString, ...] = ()
    rationale: NonEmptyString

    @model_validator(mode="after")
    def validate_expectations(self) -> Self:
        required = set(self.required_tools)
        allowed = set(self.allowed_tools)
        forbidden = set(self.forbidden_tools)
        if len(self.required_tools) != len(required):
            raise ValueError("required_tools must not contain duplicates")
        if len(self.allowed_tools) != len(allowed):
            raise ValueError("allowed_tools must not contain duplicates")
        if len(self.forbidden_tools) != len(forbidden):
            raise ValueError("forbidden_tools must not contain duplicates")
        if not required <= allowed:
            raise ValueError("allowed_tools must include every required tool")
        if allowed & forbidden:
            raise ValueError("allowed_tools and forbidden_tools must be disjoint")
        expected_documents = set(self.expected_citation_documents)
        forbidden_documents = set(self.forbidden_citation_documents)
        if expected_documents & forbidden_documents:
            raise ValueError("expected and forbidden citation documents must be disjoint")
        if self.expected_prepared_action is not None and (
            V3ToolName.PREPARE_LEAVE_REQUEST not in required
        ):
            raise ValueError("expected prepared action requires prepare_leave_request")
        if self.category is AgentCaseCategory.PROMPT_INJECTION and not (
            self.forbidden_tools or self.forbidden_calls
        ):
            raise ValueError("prompt-injection cases require a mechanical forbidden-call label")
        return self


class ToolTraceObservation(AgentEvaluationModel):
    tool_name: NonEmptyString
    arguments: dict[str, SafeArgumentValue]
    result_status: ToolResultStatus
    trusted_context_valid: bool
    employee_id_argument_present: bool
    data_kind: str | None = None


class CitationObservation(AgentEvaluationModel):
    doc_code: NonEmptyString
    title: NonEmptyString
    version: NonEmptyString
    section_anchor: NonEmptyString
    page: Annotated[int | None, Field(ge=1)] = None


class PreparedActionObservation(AgentEvaluationModel):
    type: Literal["leave_request"] = "leave_request"
    leave_type: Literal["annual"]
    start_date: date
    end_date: date
    scheduled_work_days: Annotated[int, Field(ge=0)]
    requested_hours: Decimal
    current_balance_hours: Decimal
    projected_balance_hours: Decimal
    preparation_status: LeavePreparationStatus
    reason: str | None = None
    public_holiday_check_required: bool
    non_executing: Literal[True]


class AgentCaseMetrics(AgentEvaluationModel):
    semantic_status_correct: bool
    required_tool_recall: float | None
    tool_selection_success: bool
    forbidden_tool_calls: Annotated[int, Field(ge=0)]
    unnecessary_tool_calls: Annotated[int, Field(ge=0)]
    expected_tool_outcomes_valid: bool | None
    required_citation_recall: float | None
    forbidden_citation_hits: Annotated[int, Field(ge=0)]
    citation_metadata_valid: bool | None
    citation_count_within_bound: bool
    prepared_action_presence_valid: bool
    prepared_action_structured_accuracy: float | None
    non_executing_valid: bool | None
    prepared_action_forbidden_identifiers: Annotated[int, Field(ge=0)]
    false_execution_claim: bool | None
    prompt_injection_undesired_calls: Annotated[int | None, Field(ge=0)]


class AgentInvariantMetrics(AgentEvaluationModel):
    identity_violations: Annotated[int, Field(ge=0)]
    accepted_employee_id_arguments: Annotated[int, Field(ge=0)]
    business_mutations: Annotated[int, Field(ge=0)]
    citation_count_bound_violation: bool
    tool_call_bound_violation: bool
    model_round_bound_violation: bool


class AgentCaseAttempt(AgentEvaluationModel):
    state: AgentCaseExecutionState
    safe_error_category: str | None = None
    provider_failure: AgentProviderFailureDetail | None = None


class AgentEvaluationCaseResult(AgentEvaluationModel):
    case_id: CaseId
    state: AgentCaseExecutionState
    result_origin: ResultOrigin = ResultOrigin.CURRENT_INVOCATION
    attempt_history: tuple[AgentCaseAttempt, ...] = ()
    observed_public_status: ExpectedAssistantStatus | None = None
    answer: str | None = None
    trace: tuple[ToolTraceObservation, ...] = ()
    citations: tuple[CitationObservation, ...] = ()
    prepared_action: PreparedActionObservation | None = None
    metrics: AgentCaseMetrics | None = None
    invariants: AgentInvariantMetrics | None = None
    tool_calls_attempted: Annotated[int | None, Field(ge=0)] = None
    model_rounds: Annotated[int | None, Field(ge=0)] = None
    safe_error_category: str | None = None
    provider_failure: AgentProviderFailureDetail | None = None


class AgentEvaluationConfiguration(AgentEvaluationModel):
    evaluation_schema_version: Literal["v3-agent-eval-1", "v3-agent-eval-2"] = "v3-agent-eval-2"
    agent_model: NonEmptyString
    agent_timeout_seconds: Annotated[int, Field(ge=1, le=120)] = 30
    agent_max_attempts: Annotated[int, Field(ge=1, le=3)] = 2
    trusted_evaluation_date: date
    max_tool_calls: Annotated[int, Field(gt=0)]
    max_model_rounds: Annotated[int, Field(gt=0)]
    tool_registry_fingerprint: Sha256Fingerprint
    demo_fixture_version: NonEmptyString
    grounded_generation_model: NonEmptyString
    embedding_model: NonEmptyString
    embedding_dimension: Annotated[int, Field(gt=0)]
    retrieval_top_k: Annotated[int, Field(gt=0)]
    corpus_identity: Sha256Fingerprint
    corpus_documents: Annotated[int, Field(ge=0)]
    corpus_chunks: Annotated[int, Field(ge=0)]


class AgentEvaluationSummary(AgentEvaluationModel):
    cases_total: Annotated[int, Field(ge=0)]
    cases_attempted: Annotated[int, Field(ge=0)]
    cases_completed: Annotated[int, Field(ge=0)]
    cases_provider_blocked: Annotated[int, Field(ge=0)]
    cases_error: Annotated[int, Field(ge=0)]
    cases_not_run: Annotated[int, Field(ge=0)]
    cases_carried_forward: Annotated[int, Field(ge=0)]
    cases_completed_current_invocation: Annotated[int, Field(ge=0)]
    semantic_status_accuracy: float | None
    required_tool_recall: float | None
    tool_selection_success_rate: float | None
    forbidden_tool_call_rate: float | None
    mean_tool_attempts: float | None
    unnecessary_tool_call_rate: float | None
    identity_violation_count: Annotated[int, Field(ge=0)]
    accepted_employee_id_argument_count: Annotated[int, Field(ge=0)]
    business_mutation_count: Annotated[int, Field(ge=0)]
    required_citation_recall: float | None
    forbidden_citation_hit_rate: float | None
    citation_metadata_validity_rate: float | None
    citation_count_bound_violation_count: Annotated[int, Field(ge=0)]
    prepared_action_presence_accuracy: float | None
    prepared_action_structured_accuracy: float | None
    non_executing_invariant_rate: float | None
    prepared_action_forbidden_identifier_count: Annotated[int, Field(ge=0)]
    false_execution_claim_count: Annotated[int, Field(ge=0)]
    false_execution_claim_rate: float | None
    prompt_injection_undesired_call_count: Annotated[int, Field(ge=0)]
    prompt_injection_undesired_call_rate: float | None
    tool_bound_violation_count: Annotated[int, Field(ge=0)]
    model_bound_violation_count: Annotated[int, Field(ge=0)]


class AgentEvaluationReport(AgentEvaluationModel):
    report_version: Literal["v3-stage5a-1"] = "v3-stage5a-1"
    generated_at: datetime
    mode: Literal["agent"] = "agent"
    split: EvaluationSplit
    dataset_fingerprint: DatasetFingerprint
    configuration: AgentEvaluationConfiguration
    summary: AgentEvaluationSummary
    cases: tuple[AgentEvaluationCaseResult, ...]
    no_tuning_performed: bool = True
    llm_judge_used: Literal[False] = False
