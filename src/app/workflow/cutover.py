"""Freeze 2.2 legacy execution quiescence and one-time state normalization."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.workflow.canonical import quantize_hours
from app.workflow.domain import WorkflowState
from app.workflow.errors import WorkflowIntegrityError
from app.workflow.executable_preparation import reconstruct_canonical_draft
from app.workflow.occupancy import (
    CONTRADICTORY_TERMINAL_WITH_LEAVE,
    LEGACY_UNRESOLVED_STATES,
    Phase1AInvariantError,
    assert_cutover_invariants,
    assert_phase1a_invariants,
)

AUDIT_CUTOVER_NORMALIZED = "CUTOVER_NORMALIZED"

_LEGACY_ACTIVITY_MARKERS = (
    "%action_execution_ledger%",
    "%reserve_execution%",
    "%execute_business_action%",
    "%finalize_execution%",
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


def assert_legacy_execution_quiesced(connection: Connection) -> None:
    """Fail closed if a live session still appears to be running legacy execution."""

    clauses = " OR ".join(
        f"query ILIKE :marker_{index}" for index in range(len(_LEGACY_ACTIVITY_MARKERS))
    )
    params = {f"marker_{index}": marker for index, marker in enumerate(_LEGACY_ACTIVITY_MARKERS)}
    rows = list(
        connection.execute(
            text(
                f"""
                SELECT pid, state, left(query, 180) AS query
                FROM pg_stat_activity
                WHERE datname = current_database()
                  AND pid <> pg_backend_pid()
                  AND state IN ('active', 'idle in transaction')
                  AND ({clauses})
                """
            ),
            params,
        ).mappings()
    )
    if rows:
        raise CutoverHaltError(
            tuple(
                f"legacy execution session still active pid={row['pid']} state={row['state']}"
                for row in rows
            )
        )


def normalize_legacy_execution_states(connection: Connection) -> int:
    """Normalize leftover Phase 1A states using authoritative DB evidence only."""

    now = connection.execute(text("SELECT clock_timestamp()")).scalar_one()
    rows = list(
        connection.execute(
            text(
                """
                SELECT ar.action_id, ar.state, ar.confirmed_at, ar.confirmed_expires_at,
                       ar.action_expires_at, ar.draft_payload, ar.business_request_key,
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
    """Quiesce-check, invariant-check, normalize, then re-check final invariants."""

    assert_legacy_execution_quiesced(connection)
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
    if state == WorkflowState.SUCCEEDED.value and not leaves:
        raise CutoverHaltError(
            (f"SUCCEEDED action {row['action_id']} has no valid corresponding leave result",)
        )
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
                       requested_hours, business_request_key, source_action_id
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
    return (
        leave["employee_id"] == row["owner_employee_id"]
        and leave["source_action_id"] == row["action_id"]
        and leave["leave_type"] == draft.leave_type
        and leave["start_date"] == draft.start_date
        and leave["end_date"] == draft.end_date
        and quantize_hours(leave["requested_hours"]) == draft.requested_hours
        and leave["business_request_key"] == row["business_request_key"]
    )
