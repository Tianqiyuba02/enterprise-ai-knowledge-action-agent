"""Strict M2 IT support draft, ticket, and canonical authority models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.identity import AuthenticatedEmployeeContext
from app.workflow.canonical import sha256_digest
from app.workflow.domain import ActionType

IT_RULESET_VERSION = "it-support-v1"
IT_CALENDAR_VERSION = "not-applicable"

SummaryText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=160, strict=True),
]
DescriptionText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000, strict=True),
]


class ITTicketCategory(StrEnum):
    ACCESS = "access"
    HARDWARE = "hardware"
    SOFTWARE = "software"
    NETWORK = "network"


class ITTicketUrgency(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ITTicketStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"


class PrepareITSupportTicketArguments(BaseModel):
    """Provider-facing PREPARE contract containing business fields only."""

    model_config = ConfigDict(extra="forbid", strict=True)

    category: Annotated[ITTicketCategory, Field(strict=False)]
    summary: SummaryText
    description: DescriptionText
    urgency: Annotated[ITTicketUrgency, Field(strict=False)]


class PreparedITSupportTicket(BaseModel):
    """Non-executing tool result; it carries no identity or authorization."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    category: ITTicketCategory
    summary: SummaryText
    description: DescriptionText
    urgency: ITTicketUrgency
    non_executing: Literal[True] = True


class AuthoritativeITSupportTicketDraft(BaseModel):
    """Exact persisted IT draft reviewed and authorized by an employee."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    action_type: Literal["create_it_support_ticket"] = "create_it_support_ticket"
    category: ITTicketCategory
    summary: SummaryText
    description: DescriptionText
    urgency: ITTicketUrgency
    ruleset_version: Literal["it-support-v1"] = IT_RULESET_VERSION
    authority_snapshot_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

    def payload(self) -> dict[str, object]:
        return self.model_dump(mode="json")

    def fingerprint(self) -> str:
        return it_draft_hash(self)


class ReviseITSupportTicketRequest(BaseModel):
    """Employee-editable fields plus optimistic current-revision authority."""

    model_config = ConfigDict(extra="forbid", strict=True)

    expected_revision: Annotated[int, Field(ge=1)]
    category: Annotated[ITTicketCategory, Field(strict=False)]
    summary: SummaryText
    description: DescriptionText
    urgency: Annotated[ITTicketUrgency, Field(strict=False)]


@dataclass(frozen=True, slots=True)
class ITTicketRecord:
    ticket_id: str
    employee_id: str
    owner_subject_id: str
    category: str
    summary: str
    description: str
    urgency: str
    status: str
    source_action_id: UUID | None
    source_action_revision: int | None
    created_at: datetime
    updated_at: datetime


def prepared_it_ticket(arguments: PrepareITSupportTicketArguments) -> PreparedITSupportTicket:
    return PreparedITSupportTicket(**arguments.model_dump())


def it_authority_hash(context: AuthenticatedEmployeeContext) -> str:
    if not context.employee_id or not context.subject_id or not context.jurisdiction:
        raise ValueError("complete trusted identity is required for IT authority")
    return sha256_digest(
        {
            "kind": "it_support_authority",
            "employee_id": context.employee_id,
            "owner_subject_id": context.subject_id,
            "jurisdiction": context.jurisdiction,
            "ruleset_version": IT_RULESET_VERSION,
        }
    )


def authoritative_it_draft(
    prepared: PreparedITSupportTicket | ReviseITSupportTicketRequest,
    *,
    authority_hash: str,
) -> AuthoritativeITSupportTicketDraft:
    return AuthoritativeITSupportTicketDraft(
        category=prepared.category,
        summary=prepared.summary,
        description=prepared.description,
        urgency=prepared.urgency,
        authority_snapshot_hash=authority_hash,
    )


def it_draft_hash(draft: AuthoritativeITSupportTicketDraft) -> str:
    return sha256_digest(
        {
            "kind": "it_support_draft",
            "action_type": ActionType.CREATE_IT_SUPPORT_TICKET.value,
            "category": draft.category.value,
            "summary": draft.summary,
            "description": draft.description,
            "urgency": draft.urgency.value,
            "ruleset_version": draft.ruleset_version,
            "authority_snapshot_hash": draft.authority_snapshot_hash,
        }
    )


def it_business_request_key(*, owner_subject_id: str, initiation_id: UUID) -> str:
    return sha256_digest(
        {
            "kind": "it_support_prepare_initiation",
            "action_type": ActionType.CREATE_IT_SUPPORT_TICKET.value,
            "owner_subject_id": owner_subject_id,
            "initiation_id": initiation_id,
        }
    )


def parse_authoritative_it_draft(payload: object) -> AuthoritativeITSupportTicketDraft:
    # JSONB stores StrEnum members as their wire strings. Keep provider/browser
    # input strict, while parsing the already-persisted canonical representation.
    return AuthoritativeITSupportTicketDraft.model_validate(payload, strict=False)
