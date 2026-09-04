"""M3 wrappers around sealed V4/M2 services."""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date
from typing import Any

from google.genai import types

from app.agent.loop_models import AgentModelTurn
from app.agent.provider import build_provider_function_declarations
from app.api.assistant_application import AssistantApplicationService, _is_standalone_authorization
from app.demo.service import DemoControlService
from app.workflow.action_creation import ActionCreationDisposition, ActionCreationResult

_visitor_id: ContextVar[str | None] = ContextVar("public_demo_visitor_id", default=None)
_request_deadline: ContextVar[float | None] = ContextVar("public_demo_deadline", default=None)
logger = logging.getLogger(__name__)


def _public_demo_declaration(
    declaration: types.FunctionDeclaration,
) -> types.FunctionDeclaration:
    schema = declaration.parameters_json_schema
    if not isinstance(schema, dict) or not schema.get("properties"):
        return declaration.model_copy(update={"parameters": None, "parameters_json_schema": None})
    return declaration.model_copy(
        update={
            "parameters": types.Schema.from_json_schema(
                json_schema=types.JSONSchema.model_validate(schema),
                raise_error_on_unsupported_field=True,
            ),
            "parameters_json_schema": None,
        }
    )


def _public_demo_agent_config(
    config: types.GenerateContentConfig,
) -> types.GenerateContentConfig:
    """Adapt the sealed agent contract to the current public Gemini wire schema."""

    declarations = tuple(
        _public_demo_declaration(declaration)
        for declaration in build_provider_function_declarations()
    )
    return config.model_copy(
        update={
            "temperature": None,
            "tools": [types.Tool(function_declarations=list(declarations))],
        }
    )


@contextmanager
def visitor_scope(visitor_id: str | None):
    token = _visitor_id.set(visitor_id)
    try:
        yield
    finally:
        _visitor_id.reset(token)


class QuotaActionCreationService:
    """Reserve action-preparation quota immediately before authoritative persistence."""

    def __init__(self, inner: Any, control: DemoControlService) -> None:
        self._inner = inner
        self._control = control

    def create_or_reuse(self, *args, **kwargs):
        deadline = _request_deadline.get()
        if deadline is not None and time.monotonic() >= deadline:
            return ActionCreationResult(
                disposition=ActionCreationDisposition.NOT_CREATED,
                ineligibility_reason="not_executable",
            )
        self._control.consume(visitor_id=_visitor_id.get(), metric="action_prepare")
        return self._inner.create_or_reuse(*args, **kwargs)


class MeteredAgentClient:
    """Track returned tokens and stop new model rounds at the public deadline."""

    def __init__(self, inner: Any, control: DemoControlService, deadline_seconds: int) -> None:
        self._inner = inner
        self._control = control
        self._deadline_seconds = deadline_seconds

    def start(self, user_message: str, trusted_today: date):
        deadline = _request_deadline.get() or time.monotonic() + self._deadline_seconds
        session = self._inner.start(user_message, trusted_today)
        config = getattr(session, "_config", None)
        if isinstance(config, types.GenerateContentConfig):
            session._config = _public_demo_agent_config(config)
        return _MeteredAgentSession(
            session,
            self._control,
            deadline,
        )


class _MeteredAgentSession:
    def __init__(self, inner: Any, control: DemoControlService, deadline: float) -> None:
        self._inner = inner
        self._control = control
        self._deadline = deadline

    def next(self, tool_responses=()) -> AgentModelTurn:
        if time.monotonic() >= self._deadline:
            return AgentModelTurn(
                final_text="The assistant reached its public-demo deadline. Nothing was submitted."
            )
        try:
            turn = self._inner.next(tool_responses)
        except Exception as exc:
            failure = getattr(exc, "failure", None)
            logger.warning(
                json.dumps(
                    {
                        "event": "provider_operation",
                        "service": "api",
                        "outcome": "unavailable",
                        "exception_category": type(exc).__name__,
                        "failure_kind": getattr(getattr(failure, "kind", None), "value", None),
                        "http_status_code": getattr(failure, "http_status_code", None),
                        "symbolic_status": getattr(
                            getattr(failure, "symbolic_status", None), "value", None
                        ),
                    },
                    separators=(",", ":"),
                )
            )
            raise
        usage = turn.usage
        if usage is not None:
            self._control.add_provider_tokens(usage.total_token_count)
        logger.info(
            json.dumps(
                {
                    "event": "provider_operation",
                    "service": "api",
                    "outcome": "success",
                    "total_tokens": usage.total_token_count if usage is not None else None,
                },
                separators=(",", ":"),
            )
        )
        if time.monotonic() >= self._deadline and turn.final_text is None:
            return AgentModelTurn(
                final_text="The assistant reached its public-demo deadline. Nothing was submitted.",
                usage=usage,
            )
        return turn


class PublicDemoAssistantApplicationService:
    """Apply demo quotas around the unchanged AssistantApplicationService."""

    def __init__(
        self,
        inner: AssistantApplicationService,
        control: DemoControlService,
        deadline_seconds: int,
    ) -> None:
        self._inner = inner
        self._control = control
        self._deadline_seconds = deadline_seconds

    def query(
        self,
        message: str,
        context,
        *,
        initiation_id=None,
        demo_control=None,
        visitor_id: str | None = None,
    ):
        del demo_control
        self._control.consume(visitor_id=visitor_id, metric="assistant")
        # Reserve the maximum seven bounded rounds before any provider work. This is
        # deliberately conservative and makes the global ceiling race-free.
        if not _is_standalone_authorization(message):
            self._control.consume(visitor_id=None, metric="provider_operation", amount=7)
        deadline_token = _request_deadline.set(time.monotonic() + self._deadline_seconds)
        try:
            with visitor_scope(visitor_id):
                return self._inner.query(message, context, initiation_id=initiation_id)
        finally:
            _request_deadline.reset(deadline_token)
