import json
from datetime import date
from unittest.mock import Mock

import httpx
import pytest
from google.genai import errors
from pydantic import ValidationError

from app.agent.client import (
    AgentProviderRateLimitError,
    AgentProviderTimeoutError,
    AgentProviderUnavailableError,
    GeminiAgentClient,
)
from app.agent.dispatcher import ToolDispatcher
from app.agent.errors import AssistantModelUnavailableError
from app.agent.loop_models import AgentRunResult, AgentRunStatus
from app.agent.provider_failures import (
    AgentProviderExceptionClass,
    AgentProviderFailureDetail,
    AgentProviderFailureKind,
    AgentProviderSymbolicStatus,
    classify_provider_failure,
)
from app.agent.service import AgentService
from app.api.assistant_models import AssistantQueryResponse, map_agent_result
from app.config import AgentSettings, Settings
from app.evaluation.agent_models import (
    AgentCaseAttempt,
    AgentCaseExecutionState,
    AgentEvaluationCaseResult,
)
from app.evaluation.models import ResultOrigin
from app.identity import AuthenticatedEmployeeContext


class FakeSession:
    def __init__(self, turns):
        self._turns = iter(turns)

    def next(self, _tool_responses=()):
        turn = next(self._turns)
        if isinstance(turn, Exception):
            raise turn
        return turn


class FakeProvider:
    def __init__(self, session: FakeSession):
        self.session = session

    def start(self, _user_message: str, _trusted_today: date):
        return self.session


def _api_error(
    error_cls: type[errors.APIError],
    code: int,
    status: str,
    *,
    message: str = "secret provider message",
    headers: dict[str, str] | None = None,
) -> errors.APIError:
    response = httpx.Response(
        code,
        headers=headers
        or {
            "x-request-id": "req-secret",
            "authorization": "Bearer secret-token",
        },
        json={"error": {"status": status, "message": message, "details": "secret body"}},
    )
    return error_cls(
        code,
        {
            "error": {
                "status": status,
                "message": message,
                "details": [{"reason": "secret-reason"}],
            }
        },
        response,
    )


def _assert_safe(detail: AgentProviderFailureDetail) -> None:
    dumped = detail.model_dump(mode="json")
    serialized = json.dumps(dumped)
    assert set(dumped) == {
        "kind",
        "exception_class",
        "http_status_code",
        "symbolic_status",
        "provider_error_code",
        "quota_metric",
        "quota_limit",
        "quota_limit_value",
        "quota_location",
        "retry_delay_ms",
    }
    assert "secret" not in serialized.lower()
    assert "bearer" not in serialized.lower()
    assert "req-secret" not in serialized
    assert "authorization" not in serialized
    assert "x-request-id" not in serialized
    assert "details" not in dumped
    assert "message" not in dumped
    assert "headers" not in dumped
    assert "body" not in dumped


@pytest.mark.parametrize(
    ("exc", "kind", "exception_class", "http_status_code", "symbolic_status"),
    [
        (
            _api_error(errors.ClientError, 400, "INVALID_ARGUMENT"),
            AgentProviderFailureKind.INVALID_REQUEST,
            AgentProviderExceptionClass.CLIENT_ERROR,
            400,
            AgentProviderSymbolicStatus.INVALID_ARGUMENT,
        ),
        (
            _api_error(errors.ClientError, 401, "UNAUTHENTICATED"),
            AgentProviderFailureKind.AUTHENTICATION_OR_PERMISSION,
            AgentProviderExceptionClass.CLIENT_ERROR,
            401,
            AgentProviderSymbolicStatus.UNAUTHENTICATED,
        ),
        (
            _api_error(errors.ClientError, 403, "PERMISSION_DENIED"),
            AgentProviderFailureKind.AUTHENTICATION_OR_PERMISSION,
            AgentProviderExceptionClass.CLIENT_ERROR,
            403,
            AgentProviderSymbolicStatus.PERMISSION_DENIED,
        ),
        (
            _api_error(errors.ClientError, 408, "DEADLINE_EXCEEDED"),
            AgentProviderFailureKind.TIMEOUT,
            AgentProviderExceptionClass.CLIENT_ERROR,
            408,
            AgentProviderSymbolicStatus.DEADLINE_EXCEEDED,
        ),
        (
            _api_error(errors.ClientError, 429, "RESOURCE_EXHAUSTED"),
            AgentProviderFailureKind.RATE_LIMITED,
            AgentProviderExceptionClass.CLIENT_ERROR,
            429,
            AgentProviderSymbolicStatus.RESOURCE_EXHAUSTED,
        ),
        (
            _api_error(errors.ClientError, 499, "CANCELLED"),
            AgentProviderFailureKind.CANCELLED,
            AgentProviderExceptionClass.CLIENT_ERROR,
            499,
            AgentProviderSymbolicStatus.CANCELLED,
        ),
        (
            _api_error(errors.ServerError, 500, "INTERNAL"),
            AgentProviderFailureKind.PROVIDER_UNAVAILABLE,
            AgentProviderExceptionClass.SERVER_ERROR,
            500,
            AgentProviderSymbolicStatus.INTERNAL,
        ),
        (
            _api_error(errors.ServerError, 502, "UNAVAILABLE"),
            AgentProviderFailureKind.PROVIDER_UNAVAILABLE,
            AgentProviderExceptionClass.SERVER_ERROR,
            502,
            AgentProviderSymbolicStatus.UNAVAILABLE,
        ),
        (
            _api_error(errors.ServerError, 503, "UNAVAILABLE"),
            AgentProviderFailureKind.PROVIDER_UNAVAILABLE,
            AgentProviderExceptionClass.SERVER_ERROR,
            503,
            AgentProviderSymbolicStatus.UNAVAILABLE,
        ),
        (
            _api_error(errors.ServerError, 504, "DEADLINE_EXCEEDED"),
            AgentProviderFailureKind.TIMEOUT,
            AgentProviderExceptionClass.SERVER_ERROR,
            504,
            AgentProviderSymbolicStatus.DEADLINE_EXCEEDED,
        ),
        (
            httpx.ReadTimeout("sensitive read timeout"),
            AgentProviderFailureKind.TIMEOUT,
            AgentProviderExceptionClass.READ_TIMEOUT,
            None,
            None,
        ),
        (
            httpx.ConnectTimeout("sensitive connect timeout"),
            AgentProviderFailureKind.TIMEOUT,
            AgentProviderExceptionClass.CONNECT_TIMEOUT,
            None,
            None,
        ),
        (
            httpx.ConnectError("sensitive connection failed"),
            AgentProviderFailureKind.TRANSPORT_ERROR,
            AgentProviderExceptionClass.CONNECT_ERROR,
            None,
            None,
        ),
        (
            _api_error(errors.ServerError, 418, "I_AM_A_TEAPOT"),
            AgentProviderFailureKind.UNKNOWN_PROVIDER_ERROR,
            AgentProviderExceptionClass.SERVER_ERROR,
            418,
            None,
        ),
    ],
)
def test_supported_provider_failures_are_classified_without_unsafe_content(
    exc: Exception,
    kind: AgentProviderFailureKind,
    exception_class: AgentProviderExceptionClass,
    http_status_code: int | None,
    symbolic_status: AgentProviderSymbolicStatus | None,
) -> None:
    detail = classify_provider_failure(exc)

    assert detail.kind is kind
    assert detail.exception_class is exception_class
    assert detail.http_status_code == http_status_code
    assert detail.symbolic_status is symbolic_status
    _assert_safe(detail)
    assert str(exc) not in json.dumps(detail.model_dump(mode="json"))


def test_symbolic_cancelled_without_499_is_cancelled() -> None:
    detail = classify_provider_failure(_api_error(errors.ClientError, 409, "CANCELLED"))

    assert detail.kind is AgentProviderFailureKind.CANCELLED
    assert detail.http_status_code == 409
    assert detail.symbolic_status is AgentProviderSymbolicStatus.CANCELLED
    _assert_safe(detail)


def test_unknown_exception_type_is_sanitized() -> None:
    detail = classify_provider_failure(RuntimeError("secret unexpected provider wrapper"))

    assert detail == AgentProviderFailureDetail(
        kind=AgentProviderFailureKind.UNKNOWN_PROVIDER_ERROR,
        exception_class=AgentProviderExceptionClass.UNKNOWN_EXCEPTION,
    )
    _assert_safe(detail)


def test_client_maps_api_and_httpx_errors_to_safe_exceptions() -> None:
    sdk_client = Mock()
    session = GeminiAgentClient(
        Settings(gemini_api_key="test-only-key", _env_file=None),
        AgentSettings(_env_file=None),
        sdk_client=sdk_client,
    ).start("Hello", date(2026, 8, 26))

    sdk_client.models.generate_content.side_effect = _api_error(
        errors.ClientError,
        400,
        "INVALID_ARGUMENT",
    )
    with pytest.raises(AgentProviderUnavailableError) as invalid:
        session.next()
    assert invalid.value.failure is not None
    assert invalid.value.failure.kind is AgentProviderFailureKind.INVALID_REQUEST
    assert "secret" not in str(invalid.value)

    sdk_client.models.generate_content.side_effect = httpx.ReadTimeout("sensitive provider request")
    with pytest.raises(AgentProviderTimeoutError) as timeout:
        session.next()
    assert timeout.value.failure is not None
    assert timeout.value.failure.kind is AgentProviderFailureKind.TIMEOUT
    assert timeout.value.failure.exception_class is AgentProviderExceptionClass.READ_TIMEOUT
    assert "sensitive" not in str(timeout.value)

    sdk_client.models.generate_content.side_effect = _api_error(
        errors.ClientError,
        429,
        "RESOURCE_EXHAUSTED",
    )
    with pytest.raises(AgentProviderRateLimitError) as limited:
        session.next()
    assert limited.value.failure is not None
    assert limited.value.failure.kind is AgentProviderFailureKind.RATE_LIMITED

    sdk_client.models.generate_content.side_effect = KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        session.next()


def test_agent_result_preserves_internal_failure_without_changing_public_status() -> None:
    failure = classify_provider_failure(_api_error(errors.ServerError, 504, "DEADLINE_EXCEEDED"))
    service = AgentService(
        provider=FakeProvider(
            FakeSession([AgentProviderTimeoutError("sensitive timeout", failure=failure)])
        ),
        dispatcher=Mock(spec=ToolDispatcher),
    )

    result = service.run("Hello", AuthenticatedEmployeeContext(employee_id="EMP-1001"))

    assert result.status is AgentRunStatus.PROVIDER_UNAVAILABLE
    assert result.provider_failure == failure
    assert "sensitive" not in result.model_dump_json()
    with pytest.raises(AssistantModelUnavailableError):
        map_agent_result(result)


def test_public_mapper_does_not_leak_internal_failure_fields() -> None:
    failure = AgentProviderFailureDetail(
        kind=AgentProviderFailureKind.INVALID_REQUEST,
        exception_class=AgentProviderExceptionClass.CLIENT_ERROR,
        http_status_code=400,
        symbolic_status=AgentProviderSymbolicStatus.INVALID_ARGUMENT,
    )
    result = AgentRunResult(
        status=AgentRunStatus.PROVIDER_UNAVAILABLE,
        citations=(),
        safe_message="The assistant provider is temporarily unavailable.",
        tool_calls_attempted=1,
        model_rounds=1,
        provider_failure=failure,
    )

    with pytest.raises(AssistantModelUnavailableError):
        map_agent_result(result)

    dumped = result.model_dump(mode="json")
    assert dumped["provider_failure"]["kind"] == "invalid_request"
    assert set(AssistantQueryResponse.model_fields) == {
        "status",
        "answer",
        "citations",
        "message",
        "prepared_action",
        "action",
        "action_status",
        "action_not_created_reason",
    }
    assert "provider_failure" not in AssistantQueryResponse.model_fields
    assert "http_status_code" not in AssistantQueryResponse.model_fields
    assert "exception_class" not in AssistantQueryResponse.model_fields
    assert "symbolic_status" not in AssistantQueryResponse.model_fields


def test_historical_evaluation_rows_without_failure_detail_remain_readable() -> None:
    payload = {
        "case_id": "dev_historical_blocked",
        "state": "provider_blocked",
        "result_origin": "current_invocation",
        "safe_error_category": "provider_unavailable",
    }

    result = AgentEvaluationCaseResult.model_validate(payload)
    attempt = AgentCaseAttempt.model_validate(
        {"state": "provider_blocked", "safe_error_category": "provider_unavailable"}
    )

    assert result.provider_failure is None
    assert attempt.provider_failure is None
    assert result.state is AgentCaseExecutionState.PROVIDER_BLOCKED
    assert result.result_origin is ResultOrigin.CURRENT_INVOCATION


def test_provider_failure_detail_rejects_unsafe_extra_fields() -> None:
    with pytest.raises(ValidationError):
        AgentProviderFailureDetail.model_validate(
            {
                "kind": "timeout",
                "exception_class": "ReadTimeout",
                "message": "secret",
            }
        )


def test_generic_429_stays_broad_rate_limited_without_quota_subtype() -> None:
    detail = classify_provider_failure(_api_error(errors.ClientError, 429, "RESOURCE_EXHAUSTED"))

    assert detail.kind is AgentProviderFailureKind.RATE_LIMITED
    assert detail.http_status_code == 429
    assert detail.symbolic_status is AgentProviderSymbolicStatus.RESOURCE_EXHAUSTED
    assert detail.provider_error_code is None
    assert detail.quota_metric is None
    assert detail.quota_limit is None
    assert detail.retry_delay_ms is None
    _assert_safe(detail)


def test_structured_quota_fields_are_captured_when_present() -> None:
    response = httpx.Response(429, json={"error": {"status": "RESOURCE_EXHAUSTED"}})
    exc = errors.ClientError(
        429,
        {
            "error": {
                "status": "RESOURCE_EXHAUSTED",
                "message": "secret provider quota message",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                        "reason": "RATE_LIMIT_EXCEEDED",
                        "metadata": {
                            "quota_metric": "generativelanguage.googleapis.com/generate_requests",
                            "quota_limit": "GenerateContentRequestsPerMinutePerProject",
                            "quota_limit_value": "1000",
                            "quota_location": "global",
                        },
                    },
                    {
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": "8s",
                    },
                ],
            }
        },
        response,
    )

    detail = classify_provider_failure(exc)

    assert detail.kind is AgentProviderFailureKind.RATE_LIMITED
    assert detail.provider_error_code == "rate_limit_exceeded"
    assert detail.quota_metric == "generativelanguage.googleapis.com/generate_requests"
    assert detail.quota_limit == "GenerateContentRequestsPerMinutePerProject"
    assert detail.quota_limit_value == "1000"
    assert detail.quota_location == "global"
    assert detail.retry_delay_ms == 8000
    dumped = json.dumps(detail.model_dump(mode="json"))
    assert "secret" not in dumped
    assert "message" not in detail.model_dump()
    assert "authorization" not in dumped


def test_quota_exceeded_reason_does_not_rename_rate_limited_kind() -> None:
    response = httpx.Response(429, json={"error": {"status": "RESOURCE_EXHAUSTED"}})
    exc = errors.ClientError(
        429,
        {
            "error": {
                "status": "RESOURCE_EXHAUSTED",
                "message": "secret",
                "details": [{"reason": "QUOTA_EXCEEDED"}],
            }
        },
        response,
    )

    detail = classify_provider_failure(exc)

    assert detail.kind is AgentProviderFailureKind.RATE_LIMITED
    assert detail.provider_error_code == "quota_exceeded"
