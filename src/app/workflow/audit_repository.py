"""INSERT-only audit persistence. No update or delete API."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.db.workflow_models import ActionAuditEvent
from app.workflow.domain import V4_REVISION, ActorType


@dataclass(frozen=True, slots=True)
class NewAuditEvent:
    action_id: UUID
    event_type: str
    actor_type: ActorType
    actor_subject_id: str | None = None
    from_state: str | None = None
    to_state: str | None = None
    safe_metadata: dict[str, Any] | None = None
    revision: int = V4_REVISION


FORBIDDEN_AUDIT_METADATA_KEYS = frozenset(
    {
        "token",
        "plaintext_token",
        "confirmation_token",
        "api_key",
        "gemini_api_key",
        "raw_provider_request",
        "raw_provider_response",
        "exception",
        "traceback",
    }
)


class AuditRepository:
    """Persist application-owned audit facts. Update/delete methods do not exist."""

    def insert(self, session: Session, spec: NewAuditEvent) -> ActionAuditEvent:
        metadata = spec.safe_metadata or {}
        blocked = FORBIDDEN_AUDIT_METADATA_KEYS & set(metadata)
        if blocked:
            raise ValueError(f"audit metadata must not include {sorted(blocked)}")
        row = ActionAuditEvent(
            event_id=uuid4(),
            action_id=spec.action_id,
            revision=spec.revision,
            event_type=spec.event_type,
            actor_type=spec.actor_type.value,
            actor_subject_id=spec.actor_subject_id,
            from_state=spec.from_state,
            to_state=spec.to_state,
            safe_metadata=metadata,
        )
        session.add(row)
        session.flush()
        return row
