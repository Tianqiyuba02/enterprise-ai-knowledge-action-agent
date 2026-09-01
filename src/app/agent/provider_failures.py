"""Bounded, safe internal classification of V3 outer-provider failures."""

import re
from enum import StrEnum
from typing import Annotated, Final

import httpx
from google.genai import errors
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

_HTTP_STATUS_MIN = 100
_HTTP_STATUS_MAX = 599
_SAFE_IDENTIFIER = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$", strict=True),
]
_SAFE_LIMIT_VALUE = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9._-]{1,64}$", strict=True),
]
_DURATION_RE = re.compile(r"^(\d+)(?:\.(\d{1,9}))?s$")
_MILLISECONDS_RE = re.compile(r"^(\d+)ms$")
_MAX_RETRY_DELAY_MS = 86_400_000

SAFE_PROVIDER_ERROR_CODES: Final = frozenset(
    {
        "rate_limit_exceeded",
        "quota_exceeded",
    }
)
_REASON_ALIASES: Final = {
    "RATE_LIMIT_EXCEEDED": "rate_limit_exceeded",
    "rate_limit_exceeded": "rate_limit_exceeded",
    "QUOTA_EXCEEDED": "quota_exceeded",
    "quota_exceeded": "quota_exceeded",
}


class AgentProviderFailureKind(StrEnum):
    INVALID_REQUEST = "invalid_request"
    AUTHENTICATION_OR_PERMISSION = "authentication_or_permission"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    RATE_LIMITED = "rate_limited"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    TRANSPORT_ERROR = "transport_error"
    UNKNOWN_PROVIDER_ERROR = "unknown_provider_error"


class AgentProviderSymbolicStatus(StrEnum):
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    RESOURCE_EXHAUSTED = "RESOURCE_EXHAUSTED"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    CANCELLED = "CANCELLED"
    UNAVAILABLE = "UNAVAILABLE"
    INTERNAL = "INTERNAL"
    UNKNOWN = "UNKNOWN"


class AgentProviderExceptionClass(StrEnum):
    CLIENT_ERROR = "ClientError"
    SERVER_ERROR = "ServerError"
    API_ERROR = "APIError"
    READ_TIMEOUT = "ReadTimeout"
    WRITE_TIMEOUT = "WriteTimeout"
    CONNECT_TIMEOUT = "ConnectTimeout"
    POOL_TIMEOUT = "PoolTimeout"
    TIMEOUT_EXCEPTION = "TimeoutException"
    CONNECT_ERROR = "ConnectError"
    NETWORK_ERROR = "NetworkError"
    REMOTE_PROTOCOL_ERROR = "RemoteProtocolError"
    LOCAL_PROTOCOL_ERROR = "LocalProtocolError"
    PROXY_ERROR = "ProxyError"
    READ_ERROR = "ReadError"
    WRITE_ERROR = "WriteError"
    CLOSE_ERROR = "CloseError"
    TRANSPORT_ERROR = "TransportError"
    UNKNOWN_EXCEPTION = "UnknownException"


class AgentProviderFailureDetail(BaseModel):
    """Internal-only provider failure facts; messages, bodies, and headers are excluded."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    kind: AgentProviderFailureKind
    exception_class: AgentProviderExceptionClass
    http_status_code: Annotated[int | None, Field(ge=_HTTP_STATUS_MIN, le=_HTTP_STATUS_MAX)] = None
    symbolic_status: AgentProviderSymbolicStatus | None = None
    provider_error_code: _SAFE_IDENTIFIER | None = None
    quota_metric: _SAFE_IDENTIFIER | None = None
    quota_limit: _SAFE_IDENTIFIER | None = None
    quota_limit_value: _SAFE_LIMIT_VALUE | None = None
    quota_location: _SAFE_IDENTIFIER | None = None
    retry_delay_ms: Annotated[int | None, Field(ge=0, le=_MAX_RETRY_DELAY_MS)] = None


def classify_provider_failure(exc: Exception) -> AgentProviderFailureDetail:
    """Map a supported provider/transport exception to a sanitized diagnostic record."""

    exception_class = _sanitized_exception_class(exc)
    if isinstance(exc, httpx.TimeoutException):
        return AgentProviderFailureDetail(
            kind=AgentProviderFailureKind.TIMEOUT,
            exception_class=exception_class,
        )
    if isinstance(exc, httpx.TransportError):
        return AgentProviderFailureDetail(
            kind=AgentProviderFailureKind.TRANSPORT_ERROR,
            exception_class=exception_class,
        )
    if isinstance(exc, errors.APIError):
        http_status_code = _sanitized_http_status(exc)
        symbolic_status = _sanitized_symbolic_status(exc)
        extras = extract_structured_provider_diagnostics(exc)
        return AgentProviderFailureDetail(
            kind=_classify_api_error(http_status_code, symbolic_status),
            exception_class=exception_class,
            http_status_code=http_status_code,
            symbolic_status=symbolic_status,
            **extras,
        )
    return AgentProviderFailureDetail(
        kind=AgentProviderFailureKind.UNKNOWN_PROVIDER_ERROR,
        exception_class=exception_class,
    )


def extract_structured_provider_diagnostics(exc: errors.APIError) -> dict[str, object]:
    """Copy only allowlisted structured quota/retry fields. Absence stays explicit null."""

    extras: dict[str, object] = {
        "provider_error_code": None,
        "quota_metric": None,
        "quota_limit": None,
        "quota_limit_value": None,
        "quota_location": None,
        "retry_delay_ms": None,
    }
    details = getattr(exc, "details", None)
    if not isinstance(details, dict):
        return extras
    error = details.get("error")
    payload = error if isinstance(error, dict) else details
    if not isinstance(payload, dict):
        return extras
    entries = payload.get("details")
    if not isinstance(entries, list):
        return extras
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        reason = _safe_provider_error_code(entry.get("reason"))
        if reason is not None and extras["provider_error_code"] is None:
            extras["provider_error_code"] = reason
        metadata = entry.get("metadata")
        if isinstance(metadata, dict):
            extras["quota_metric"] = extras["quota_metric"] or _safe_identifier(
                metadata.get("quota_metric") or metadata.get("quotaMetric")
            )
            extras["quota_limit"] = extras["quota_limit"] or _safe_identifier(
                metadata.get("quota_limit") or metadata.get("quotaLimit")
            )
            extras["quota_limit_value"] = extras["quota_limit_value"] or _safe_limit_value(
                metadata.get("quota_limit_value") or metadata.get("quotaLimitValue")
            )
            extras["quota_location"] = extras["quota_location"] or _safe_identifier(
                metadata.get("quota_location") or metadata.get("quotaLocation")
            )
        if extras["retry_delay_ms"] is None:
            extras["retry_delay_ms"] = _safe_retry_delay_ms(
                entry.get("retryDelay") if "retryDelay" in entry else entry.get("retry_delay")
            )
    return extras


def _classify_api_error(
    http_status_code: int | None,
    symbolic_status: AgentProviderSymbolicStatus | None,
) -> AgentProviderFailureKind:
    if http_status_code == 400:
        return AgentProviderFailureKind.INVALID_REQUEST
    if http_status_code in {401, 403}:
        return AgentProviderFailureKind.AUTHENTICATION_OR_PERMISSION
    if http_status_code == 408:
        return AgentProviderFailureKind.TIMEOUT
    if http_status_code == 429:
        return AgentProviderFailureKind.RATE_LIMITED
    if http_status_code == 499 or symbolic_status is AgentProviderSymbolicStatus.CANCELLED:
        return AgentProviderFailureKind.CANCELLED
    if http_status_code in {500, 502, 503}:
        return AgentProviderFailureKind.PROVIDER_UNAVAILABLE
    if http_status_code == 504:
        return AgentProviderFailureKind.TIMEOUT
    if symbolic_status is AgentProviderSymbolicStatus.RESOURCE_EXHAUSTED:
        return AgentProviderFailureKind.RATE_LIMITED
    if symbolic_status is AgentProviderSymbolicStatus.DEADLINE_EXCEEDED:
        return AgentProviderFailureKind.TIMEOUT
    if symbolic_status is AgentProviderSymbolicStatus.INVALID_ARGUMENT:
        return AgentProviderFailureKind.INVALID_REQUEST
    if symbolic_status in {
        AgentProviderSymbolicStatus.UNAUTHENTICATED,
        AgentProviderSymbolicStatus.PERMISSION_DENIED,
    }:
        return AgentProviderFailureKind.AUTHENTICATION_OR_PERMISSION
    return AgentProviderFailureKind.UNKNOWN_PROVIDER_ERROR


def _sanitized_exception_class(exc: Exception) -> AgentProviderExceptionClass:
    try:
        return AgentProviderExceptionClass(type(exc).__name__)
    except ValueError:
        return AgentProviderExceptionClass.UNKNOWN_EXCEPTION


def _sanitized_http_status(exc: errors.APIError) -> int | None:
    raw_code = getattr(exc, "code", None)
    try:
        code = int(raw_code) if raw_code not in {None, ""} else None
    except (TypeError, ValueError):
        return None
    if code is None or not _HTTP_STATUS_MIN <= code <= _HTTP_STATUS_MAX:
        return None
    return code


def _sanitized_symbolic_status(exc: errors.APIError) -> AgentProviderSymbolicStatus | None:
    raw_status = getattr(exc, "status", None)
    if not isinstance(raw_status, str):
        return None
    try:
        return AgentProviderSymbolicStatus(raw_status.strip().upper())
    except ValueError:
        return None


def _safe_provider_error_code(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = _REASON_ALIASES.get(value.strip(), None)
    if normalized in SAFE_PROVIDER_ERROR_CODES:
        return normalized
    return None


def _safe_identifier(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}", stripped) is None:
        return None
    return stripped


def _safe_limit_value(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        text = str(value)
    elif isinstance(value, str):
        text = value.strip()
    else:
        return None
    if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", text) is None:
        return None
    return text


def _safe_retry_delay_ms(value: object) -> int | None:
    if isinstance(value, dict):
        seconds = value.get("seconds")
        nanos = value.get("nanos", 0)
        if not isinstance(seconds, int) or isinstance(seconds, bool):
            return None
        if nanos is None:
            nanos = 0
        if not isinstance(nanos, int) or isinstance(nanos, bool) or nanos < 0:
            return None
        millis = seconds * 1000 + nanos // 1_000_000
        return millis if 0 <= millis <= _MAX_RETRY_DELAY_MS else None
    if isinstance(value, str):
        stripped = value.strip()
        duration = _DURATION_RE.fullmatch(stripped)
        if duration is not None:
            whole = int(duration.group(1))
            fraction = duration.group(2) or "0"
            millis = whole * 1000 + int((fraction + "000000000")[:3])
            return millis if 0 <= millis <= _MAX_RETRY_DELAY_MS else None
        milliseconds = _MILLISECONDS_RE.fullmatch(stripped)
        if milliseconds is not None:
            millis = int(milliseconds.group(1))
            return millis if 0 <= millis <= _MAX_RETRY_DELAY_MS else None
    return None
