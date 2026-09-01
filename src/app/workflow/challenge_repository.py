"""Confirmation-challenge persistence and query primitives. No token issuance."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.workflow_models import ConfirmationChallenge
from app.workflow.domain import V4_REVISION, ChallengeStatus


@dataclass(frozen=True, slots=True)
class NewConfirmationChallenge:
    action_id: UUID
    owner_subject_id: str
    confirmation_session_id: str
    draft_hash: str
    token_hash: str
    issued_at: datetime
    expires_at: datetime
    revision: int = V4_REVISION
    status: ChallengeStatus = ChallengeStatus.ACTIVE


class ChallengeRepository:
    """Store and look up challenges. Callers never pass a plaintext token."""

    def persist(self, session: Session, spec: NewConfirmationChallenge) -> ConfirmationChallenge:
        row = ConfirmationChallenge(
            challenge_id=uuid4(),
            action_id=spec.action_id,
            revision=spec.revision,
            owner_subject_id=spec.owner_subject_id,
            confirmation_session_id=spec.confirmation_session_id,
            draft_hash=spec.draft_hash,
            token_hash=spec.token_hash,
            status=spec.status.value,
            issued_at=spec.issued_at,
            expires_at=spec.expires_at,
        )
        session.add(row)
        session.flush()
        return row

    def get(self, session: Session, challenge_id: UUID) -> ConfirmationChallenge | None:
        return session.get(ConfirmationChallenge, challenge_id)

    def get_active_challenge(
        self,
        session: Session,
        *,
        action_id: UUID,
        revision: int = V4_REVISION,
    ) -> ConfirmationChallenge | None:
        return session.execute(
            select(ConfirmationChallenge).where(
                ConfirmationChallenge.action_id == action_id,
                ConfirmationChallenge.revision == revision,
                ConfirmationChallenge.status == ChallengeStatus.ACTIVE.value,
            )
        ).scalar_one_or_none()

    def lock_challenge(
        self,
        session: Session,
        challenge_id: UUID,
    ) -> ConfirmationChallenge | None:
        return session.execute(
            select(ConfirmationChallenge)
            .where(ConfirmationChallenge.challenge_id == challenge_id)
            .with_for_update()
        ).scalar_one_or_none()

    def lock_active_challenge(
        self,
        session: Session,
        *,
        action_id: UUID,
        revision: int = V4_REVISION,
    ) -> ConfirmationChallenge | None:
        return session.execute(
            select(ConfirmationChallenge)
            .where(
                ConfirmationChallenge.action_id == action_id,
                ConfirmationChallenge.revision == revision,
                ConfirmationChallenge.status == ChallengeStatus.ACTIVE.value,
            )
            .with_for_update()
        ).scalar_one_or_none()
