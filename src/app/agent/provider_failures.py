"""Bounded, safe internal classification of V3 outer-provider failures."""

from enum import StrEnum
from typing import Annotated

import httpx
from google.genai import errors
from pydantic import BaseModel, ConfigDict, Field

_HTTP_STATUS_MIN = 100
_HTTP_STATUS_MAX = 599


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
        return AgentProviderFailureDetail(
            kind=_classify_api_error(http_status_code, symbolic_status),
            exception_class=exception_class,
            http_status_code=http_status_code,
            symbolic_status=symbolic_status,
        )
    return AgentProviderFailureDetail(
        kind=AgentProviderFailureKind.UNKNOWN_PROVIDER_ERROR,
        exception_class=exception_class,
    )


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
