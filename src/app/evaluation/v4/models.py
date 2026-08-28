"""Versioned V4 product-evaluation contracts. Do not mutate V3 agent-eval semantics."""

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.agent.provider_failures import AgentProviderFailureDetail
from app.evaluation.models import CaseId, DatasetFingerprint, ResultOrigin

V4_EVALUATOR_VERSION: Literal["v4-product-eval-1"] = "v4-product-eval-1"
V4_DEVELOPMENT_SET_VERSION: Literal["v4-product-dev-1"] = "v4-product-dev-1"
V4_REPORT_VERSION: Literal["v4-product-eval-1"] = "v4-product-eval-1"

NonEmptyString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, strict=True),
]
Sha256Fingerprint = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{64}$", strict=True),
]
UserMessage = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4_000, strict=True),
]


class V4EvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class V4CaseSplit(StrEnum):
    DEVELOPMENT = "development"


class V4CaseCategory(StrEnum):
    EXECUTABLE_PREPARE = "executable_prepare"
    NON_EXECUTABLE = "non_executable"
    READ_ONLY = "read_only"
    AUTHORITY_ABUSE = "authority_abuse"
    REUSE_LIFECYCLE = "reuse_lifecycle"
    FULL_E2E = "full_e2e"


class V4EmployeeFixture(StrEnum):
    ALEX = "alex"


class V4PrepareExpectation(StrEnum):
    REQUIRED = "required"
    FORBIDDEN = "forbidden"
    OPTIONAL = "optional"


class V4SetupKind(StrEnum):
    NONE = "none"
    SEED_AWAITING = "seed_awaiting"
    SEED_UNKNOWN = "seed_unknown"
    SEED_SUCCEEDED = "seed_succeeded"


class V4ExpectedActionStatus(StrEnum):
    NONE = "none"
    NOT_CREATED = "not_created"
    CREATED = "created"
    REUSED = "reused"


class V4CaseExecutionState(StrEnum):
    COMPLETED = "completed"
    PROVIDER_BLOCKED = "provider_blocked"
    ERROR = "error"


class V4SetupState(V4EvaluationModel):
    kind: V4SetupKind = V4SetupKind.NONE
    start_date: date | None = None
    end_date: date | None = None
    reason: str | None = None
    include_leave_request: bool = False

    @model_validator(mode="after")
    def validate_seed_dates(self) -> Self:
        if self.kind is V4SetupKind.NONE:
            return self
        if self.start_date is None or self.end_date is None:
            raise ValueError("seeded setup requires start_date and end_date")
        if self.start_date > self.end_date:
            raise ValueError("setup start_date must not be after end_date")
        return self


class V4ExpectedModelBehavior(V4EvaluationModel):
    prepare_expectation: V4PrepareExpectation
    prepared_start_date: date | None = None
    prepared_end_date: date | None = None
    expect_citations: bool | None = None


class V4ExpectedProductBehavior(V4EvaluationModel):
    action_status: V4ExpectedActionStatus
    action_state: str | None = None
    action_not_created_reason: str | None = None
    confirmation_required: bool | None = None
    confirmation_side_effect: bool = False
    authoritative_requested_hours: str | None = None
    authoritative_reason: str | None = None
    reuse_same_action: bool = False
    retain_persisted_reason: bool = False


class V4ExpectedBusinessBehavior(V4EvaluationModel):
    leave_request_count: int | None = None
    execution_ledger_count: int | None = None
    final_state: str | None = None
    no_duplicate_leave: bool = False


class V4ProductEvaluationCase(V4EvaluationModel):
    id: CaseId
    split: Literal[V4CaseSplit.DEVELOPMENT] = V4CaseSplit.DEVELOPMENT
    description: NonEmptyString
    category: V4CaseCategory
    employee_fixture: V4EmployeeFixture = V4EmployeeFixture.ALEX
    dataset_kind: Literal["DEVELOPMENT"] = "DEVELOPMENT"
    assistant_prompts: tuple[UserMessage, ...]
    setup_state: V4SetupState = Field(default_factory=V4SetupState)
    provider_required: bool = True
    out_of_band_confirmation: bool = False
    worker_execution: bool = False
    action_authority_applicable: bool = False
    prompt_injection_applicable: bool = False
    expected_model_behavior: V4ExpectedModelBehavior
    expected_product_behavior: V4ExpectedProductBehavior
    expected_business_behavior: V4ExpectedBusinessBehavior = Field(
        default_factory=V4ExpectedBusinessBehavior
    )
    rationale: NonEmptyString

    @model_validator(mode="after")
    def validate_case_shape(self) -> Self:
        if not self.assistant_prompts:
            raise ValueError("at least one assistant prompt is required")
        if self.out_of_band_confirmation is not self.worker_execution:
            raise ValueError("confirmation and worker execution must be paired")
        if self.category is V4CaseCategory.FULL_E2E and not self.out_of_band_confirmation:
            raise ValueError("full E2E cases must include out-of-band confirmation")
        if self.prompt_injection_applicable and not self.action_authority_applicable:
            raise ValueError("prompt-injection cases must label action-authority applicability")
        return self


class V4ModelObservation(V4EvaluationModel):
    provider_completed: bool
    provider_blocked: bool
    provider_failure_category: str | None = None
    provider_failure: AgentProviderFailureDetail | None = None
    assistant_status: str | None = None
    answer: str | None = None
    prepared_action_present: bool
    prepared_action_authority: str | None = None
    prepared_start_date: date | None = None
    prepared_end_date: date | None = None
    citation_count: int = 0
    citation_doc_codes: tuple[str, ...] = ()
    latency_ms: int | None = None
    tool_names: tuple[str, ...] = ()


class V4ProductObservation(V4EvaluationModel):
    action_status: str | None = None
    action_not_created_reason: str | None = None
    action_id: str | None = None
    action_state: str | None = None
    action_authority: str | None = None
    confirmation_required: bool | None = None
    draft_requested_hours: str | None = None
    draft_reason: str | None = None
    draft_start_date: str | None = None
    same_action_reused: bool | None = None
    challenge_count: int = 0
    confirmation_outbox_count: int = 0
    execution_ledger_count: int = 0
    chat_caused_authority_transition: bool = False


class V4BusinessObservation(V4EvaluationModel):
    final_state: str | None = None
    leave_request_count: int = 0
    execution_ledger_count: int = 0
    business_request_key: str | None = None
    duplicate_leave_created: bool = False
    created_or_adopted: str | None = None


class V4SafetyFlags(V4EvaluationModel):
    confirmation_bypass_violation: bool = False
    action_authority_violation: bool = False
    unauthorized_execution_violation: bool = False
    duplicate_live_action_violation: bool = False
    duplicate_business_mutation_violation: bool = False
    non_executable_action_creation_violation: bool = False
    wrong_owner_authority_violation: bool | None = None


class V4CaseJudgement(V4EvaluationModel):
    model_behavior_pass: bool | None = None
    product_behavior_pass: bool | None = None
    product_safety_pass: bool | None = None
    business_behavior_pass: bool | None = None
    semantic_pass: bool | None = None


class V4CaseAttempt(V4EvaluationModel):
    state: V4CaseExecutionState
    safe_error_category: str | None = None
    provider_failure_category: str | None = None


class V4ProductCaseResult(V4EvaluationModel):
    case_id: CaseId
    state: V4CaseExecutionState
    result_origin: ResultOrigin = ResultOrigin.CURRENT_INVOCATION
    attempt_history: tuple[V4CaseAttempt, ...] = ()
    model: V4ModelObservation | None = None
    product: V4ProductObservation | None = None
    business: V4BusinessObservation | None = None
    safety: V4SafetyFlags | None = None
    judgement: V4CaseJudgement | None = None
    safe_error_category: str | None = None


class V4EvaluationFingerprints(V4EvaluationModel):
    development_set: Sha256Fingerprint
    agent_policy: Sha256Fingerprint
    product_code: Sha256Fingerprint


class V4EvaluationConfiguration(V4EvaluationModel):
    evaluator_version: Literal["v4-product-eval-1"] = V4_EVALUATOR_VERSION
    development_set_version: Literal["v4-product-dev-1"] = V4_DEVELOPMENT_SET_VERSION
    agent_model: NonEmptyString
    agent_timeout_seconds: Annotated[int, Field(ge=1, le=120)]
    agent_max_attempts: Annotated[int, Field(ge=1, le=3)]
    trusted_evaluation_date: date
    corpus_documents: Annotated[int, Field(ge=0)]
    corpus_chunks: Annotated[int, Field(ge=0)]
    holiday_rows: Annotated[int, Field(ge=0)]
    calendar_version: NonEmptyString
    fingerprints: V4EvaluationFingerprints


class V4EvaluationSummary(V4EvaluationModel):
    cases_total: Annotated[int, Field(ge=0)]
    provider_completed_count: Annotated[int, Field(ge=0)]
    provider_blocked_count: Annotated[int, Field(ge=0)]
    provider_block_rate: float | None
    cases_error: Annotated[int, Field(ge=0)]
    cases_carried_forward: Annotated[int, Field(ge=0)]
    semantic_evaluable_count: Annotated[int, Field(ge=0)]
    case_semantic_pass_rate: float | None
    prepare_expectation_accuracy: float | None
    read_no_action_accuracy: float | None
    action_outcome_accuracy: float | None
    authoritative_draft_accuracy: float | None
    action_reuse_accuracy: float | None
    confirmation_bypass_violation_rate: float | None
    action_authority_violation_rate: float | None
    unauthorized_execution_violation_rate: float | None
    duplicate_live_action_violation_rate: float | None
    duplicate_business_mutation_violation_rate: float | None
    non_executable_action_creation_violation_rate: float | None
    prompt_injection_or_action_authority_violation_rate: float | None
    full_e2e_success_rate: float | None
    safety_gate_failed: bool


class V4ProductEvaluationReport(V4EvaluationModel):
    report_version: Literal["v4-product-eval-1"] = V4_REPORT_VERSION
    evaluator_version: Literal["v4-product-eval-1"] = V4_EVALUATOR_VERSION
    development_set_version: Literal["v4-product-dev-1"] = V4_DEVELOPMENT_SET_VERSION
    generated_at: datetime
    branch: NonEmptyString
    commit: NonEmptyString
    split: Literal["development"] = "development"
    dataset_kind: Literal["DEVELOPMENT"] = "DEVELOPMENT"
    dataset_fingerprint: DatasetFingerprint
    configuration: V4EvaluationConfiguration
    summary: V4EvaluationSummary
    cases: tuple[V4ProductCaseResult, ...]
    llm_judge_used: Literal[False] = False
    holdout_used: Literal[False] = False
    no_tuning_performed: bool = True
