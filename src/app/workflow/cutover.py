"""Freeze 2.2 maintenance-mode execution cutover.

Supported 0005 procedure:

1. stop ALL old application / worker processes
2. prevent automatic restart
3. confirm no old application execution transaction remains
4. enter maintenance/no-write cutover window
5. run authoritative invariant scan + normalization + 0005 migration
6. start ONLY the new binary

Operational process quiescence is a deployment precondition. This module does
not inspect pg_stat_activity or treat SQL-text markers as proof that old
binaries have stopped. An old binary is controlled by that procedure, not by
new fencing machinery.

The migration fails closed on contradictory persisted evidence. The new binary
hard-refuses leftover legacy execution mutation entrypoints.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.workflow.domain import V4_REVISION, ChallengeStatus, WorkflowState
from app.workflow.errors import WorkflowIntegrityError
from app.workflow.executable_preparation import reconstruct_canonical_draft
from app.workflow.leave_equivalence import leaves_trusted_equivalent
from app.workflow.occupancy import (
    CONTRADICTORY_TERMINAL_WITH_LEAVE,
    LEGACY_UNRESOLVED_STATES,
    Phase1AInvariantError,
    assert_cutover_invariants,
    assert_phase1a_invariants,
)

AUDIT_CUTOVER_NORMALIZED = "CUTOVER_NORMALIZED"
AUDIT_CHALLENGE_SUPERSEDED = "CHALLENGE_SUPERSEDED"

MAINTENANCE_CUTOVER_PROCEDURE: Final[tuple[str, ...]] = (
    "stop ALL old application / worker processes",
    "prevent automatic restart",
    "confirm no old application execution transaction remains",
    "enter maintenance/no-write cutover window",
    "run authoritative invariant scan + normalization + 0005 migration",
    "start ONLY the new binary",
)


class LegacyExecutionQuiescedError(RuntimeError):
    """Raised when a caller tries to schedule or run the retired execution path."""

    def __init__(self) -> None:
        super().__init__(
            "legacy execution scheduling is quiesced after simplified execution cutover"
        )


class CutoverHaltError(RuntimeError):
    """Raised when cutover finds contradictory history that must not be repaired."""

    def __init__(self, findings: tuple[str, ...]) -> None:
        self.findings = findings
        super().__init__("execution cutover halted:\n" + "\n".join(findings))


def refuse_legacy_execution_scheduling() -> None:
    raise LegacyExecutionQuiescedError()


def normalize_legacy_execution_states(connection: Connection) -> int:
    """Normalize leftover Phase 1A states using authoritative DB evidence only."""

    now = connection.execute(text("SELECT clock_timestamp()")).scalar_one()
    rows = list(
        connection.execute(
            text(
                """
                SELECT ar.action_id, ar.state, ar.confirmed_at, ar.confirmed_expires_at,
                       ar.action_expires_at, ar.draft_payload, ar.business_request_key,
                       ar.calendar_version, ar.ruleset_version, ar.revision,
                       aw.owner_employee_id
                FROM action_revisions ar
                JOIN action_workflows aw ON aw.action_id = ar.action_id
                ORDER BY ar.action_id
                """
            )
        ).mappings()
    )
    changed = 0
    for row in rows:
        target = _normalized_state(connection, row, now)
        if target is None or target == row["state"]:
            continue
        connection.execute(
            text(
                """
                UPDATE action_revisions
                SET state = :state, updated_at = clock_timestamp()
                WHERE action_id = :action_id AND revision = 1
                """
            ),
            {"state": target, "action_id": row["action_id"]},
        )
        if (
            row["state"] == WorkflowState.AWAITING_CONFIRMATION.value
            and target == WorkflowState.EXPIRED.value
        ):
            _supersede_active_challenges(connection, row["action_id"], now)
        connection.execute(
            text(
                """
                INSERT INTO action_audit_events (
                    event_id, action_id, revision, event_type, actor_type,
                    from_state, to_state, safe_metadata
                ) VALUES (
                    gen_random_uuid(), :action_id, 1, :event_type, 'system',
                    :from_state, :to_state, CAST(:metadata AS jsonb)
                )
                """
            ),
            {
                "action_id": row["action_id"],
                "event_type": AUDIT_CUTOVER_NORMALIZED,
                "from_state": row["state"],
                "to_state": target,
                "metadata": '{"reason":"legacy_state_normalization"}',
            },
        )
        changed += 1
    return changed


def run_execution_cutover_preflight(connection: Connection) -> int:
    """Invariant-check, normalize, then re-check final invariants.

    Does not attempt to prove that old application processes have stopped.
    That is a maintenance-window deployment precondition.
    """

    assert_phase1a_invariants(connection)
    changed = normalize_legacy_execution_states(connection)
    assert_cutover_invariants(connection)
    leftover = connection.execute(
        text(
            """
            SELECT count(*)
            FROM action_revisions
            WHERE state IN (:executing, :unknown, :reconciling)
            """
        ),
        {
            "executing": WorkflowState.EXECUTING.value,
            "unknown": WorkflowState.UNKNOWN_OUTCOME.value,
            "reconciling": WorkflowState.RECONCILING.value,
        },
    ).scalar_one()
    if leftover:
        raise Phase1AInvariantError(
            (f"{leftover} legacy unresolved action_revisions remain after normalization",)
        )
    return changed


def _normalized_state(connection: Connection, row, now) -> str | None:
    state = row["state"]
    leaves = _matching_leaves(connection, row["action_id"], row["business_request_key"])
    if (
        state
        in (
            WorkflowState.AWAITING_CONFIRMATION.value,
            WorkflowState.CONFIRMED.value,
        )
        and leaves
    ):
        raise CutoverHaltError(
            (
                f"{state} action {row['action_id']} has a committed leave "
                "and must not be auto-repaired",
            ),
        )
    if state in CONTRADICTORY_TERMINAL_WITH_LEAVE and leaves:
        raise CutoverHaltError(
            (
                f"{state} action {row['action_id']} has a committed leave "
                "and must not be auto-repaired",
            ),
        )
    if state == WorkflowState.SUCCEEDED.value:
        if len(leaves) != 1 or not _trusted_equivalent_leave(row, leaves[0]):
            raise CutoverHaltError(
                (f"SUCCEEDED action {row['action_id']} has no valid corresponding leave result",)
            )
        return None
    if state == WorkflowState.AWAITING_CONFIRMATION.value:
        return (
            WorkflowState.EXPIRED.value
            if _awaiting_expired(row, now)
            else WorkflowState.AWAITING_CONFIRMATION.value
        )
    if state == WorkflowState.CONFIRMED.value:
        return (
            WorkflowState.EXPIRED.value
            if _confirmed_expired(row, now)
            else WorkflowState.CONFIRMED.value
        )
    if state in LEGACY_UNRESOLVED_STATES:
        if not leaves:
            if _confirmed_expired(row, now):
                return WorkflowState.EXPIRED.value
            return WorkflowState.CONFIRMED.value
        if len(leaves) == 1 and _trusted_equivalent_leave(row, leaves[0]):
            return WorkflowState.SUCCEEDED.value
        raise CutoverHaltError(
            (f"{state} action {row['action_id']} has contradictory or ambiguous leave evidence",)
        )
    return None


def _awaiting_expired(row, now) -> bool:
    expires = row["action_expires_at"]
    if expires is None:
        raise CutoverHaltError(
            (f"AWAITING_CONFIRMATION action {row['action_id']} has null action_expires_at",)
        )
    return expires <= now


def _confirmed_expired(row, now) -> bool:
    expires = row["confirmed_expires_at"]
    if expires is None:
        if row["state"] == WorkflowState.CONFIRMED.value:
            raise CutoverHaltError(
                (f"CONFIRMED action {row['action_id']} has null confirmed_expires_at",)
            )
        action_expires = row["action_expires_at"]
        if action_expires is None:
            raise CutoverHaltError(
                (f"{row['state']} action {row['action_id']} has no authoritative TTL",)
            )
        return action_expires <= now
    return expires <= now


def _matching_leaves(connection: Connection, action_id, business_request_key: str) -> list:
    return list(
        connection.execute(
            text(
                """
                SELECT leave_request_id, employee_id, leave_type, start_date, end_date,
                       requested_hours, reason, status, business_request_key,
                       source_action_id, source_action_revision, calendar_version,
                       ruleset_version
                FROM leave_requests
                WHERE source_action_id = :action_id
                   OR business_request_key = :business_request_key
                ORDER BY leave_request_id
                """
            ),
            {"action_id": action_id, "business_request_key": business_request_key},
        ).mappings()
    )


def _trusted_equivalent_leave(row, leave) -> bool:
    payload = row["draft_payload"]
    if not isinstance(payload, dict):
        return False
    try:
        draft = reconstruct_canonical_draft(payload)
    except (WorkflowIntegrityError, TypeError, ValueError):
        return False
    return leaves_trusted_equivalent(
        employee_id=row["owner_employee_id"],
        action_id=row["action_id"],
        revision=int(row["revision"] or V4_REVISION),
        leave_type=draft.leave_type,
        start_date=draft.start_date,
        end_date=draft.end_date,
        requested_hours=draft.requested_hours,
        business_request_key=row["business_request_key"],
        reason=draft.reason,
        calendar_version=row["calendar_version"],
        ruleset_version=row["ruleset_version"],
        leave=leave,
    )


def _supersede_active_challenges(connection: Connection, action_id, now) -> None:
    challenges = list(
        connection.execute(
            text(
                """
                UPDATE confirmation_challenges
                SET status = :superseded, superseded_at = :now
                WHERE action_id = :action_id AND status = :active
                RETURNING challenge_id
                """
            ),
            {
                "action_id": action_id,
                "now": now,
                "superseded": ChallengeStatus.SUPERSEDED.value,
                "active": ChallengeStatus.ACTIVE.value,
            },
        ).mappings()
    )
    for challenge in challenges:
        connection.execute(
            text(
                """
                INSERT INTO action_audit_events (
                    event_id, action_id, revision, event_type, actor_type,
                    from_state, to_state, safe_metadata
                ) VALUES (
                    gen_random_uuid(), :action_id, 1, :event_type, 'system',
                    :from_state, :to_state, CAST(:metadata AS jsonb)
                )
                """
            ),
            {
                "action_id": action_id,
                "event_type": AUDIT_CHALLENGE_SUPERSEDED,
                "from_state": WorkflowState.AWAITING_CONFIRMATION.value,
                "to_state": WorkflowState.EXPIRED.value,
                "metadata": f'{{"challenge_id":"{challenge["challenge_id"]}"}}',
            },
        )
