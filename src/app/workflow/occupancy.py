"""Phase 1A transitional occupancy constants and fail-closed invariant queries."""

from __future__ import annotations

from typing import Final

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from app.workflow.canonical import business_request_key
from app.workflow.domain import ActionType, ChallengeStatus, LeaveType, WorkflowState
from app.workflow.errors import WorkflowIntegrityError
from app.workflow.executable_preparation import reconstruct_canonical_draft

OCCUPANCY_UNIQUE_INDEX: Final = "uq_action_revisions_occupying_business_request_key"
SOURCE_ACTION_UNIQUE_CONSTRAINT: Final = "uq_leave_requests_source_action_id"
FINAL_OCCUPANCY_UNIQUE_INDEX: Final = "uq_action_revisions_final_occupying_business_request_key"

TRANSITIONAL_OCCUPANCY_STATES: Final = (
    WorkflowState.AWAITING_CONFIRMATION.value,
    WorkflowState.CONFIRMED.value,
    WorkflowState.EXECUTING.value,
    WorkflowState.UNKNOWN_OUTCOME.value,
    WorkflowState.RECONCILING.value,
    WorkflowState.SUCCEEDED.value,
)
PREPARE_NORMALIZABLE_STATES: Final = frozenset(
    {
        WorkflowState.AWAITING_CONFIRMATION.value,
        WorkflowState.CONFIRMED.value,
    }
)
LEGACY_UNRESOLVED_STATES: Final = frozenset(
    {
        WorkflowState.EXECUTING.value,
        WorkflowState.UNKNOWN_OUTCOME.value,
        WorkflowState.RECONCILING.value,
    }
)
CONTRADICTORY_TERMINAL_WITH_LEAVE: Final = frozenset(
    {
        WorkflowState.EXECUTION_FAILED.value,
        WorkflowState.CANCELLED.value,
        WorkflowState.EXPIRED.value,
        WorkflowState.STALE.value,
    }
)
NONTERMINAL_CONFIRMATION_STATES: Final = frozenset(
    {
        WorkflowState.AWAITING_CONFIRMATION.value,
        WorkflowState.CONFIRMED.value,
    }
)


class Phase1AInvariantError(RuntimeError):
    """Raised when Phase 1A preflight finds contradictory or duplicate data."""

    def __init__(self, findings: tuple[str, ...]) -> None:
        self.findings = findings
        super().__init__("Phase 1A invariant gate failed:\n" + "\n".join(findings))


def occupancy_where_sql() -> str:
    quoted = ", ".join(f"'{state}'" for state in TRANSITIONAL_OCCUPANCY_STATES)
    return f"state IN ({quoted})"


def is_occupancy_unique_violation(exc: BaseException) -> bool:
    """Return True only for the named transitional occupancy unique conflict."""

    return _named_unique_violation(exc, OCCUPANCY_UNIQUE_INDEX)


def _named_unique_violation(exc: BaseException, constraint_name: str) -> bool:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        diag = getattr(current, "diag", None)
        if getattr(diag, "constraint_name", None) == constraint_name:
            return True
        if isinstance(current, IntegrityError):
            orig = getattr(current, "orig", None)
            if orig is not None and _named_unique_violation(orig, constraint_name):
                return True
        current = current.__cause__ or current.__context__
    return False


def collect_phase1a_invariant_violations(connection: Connection) -> tuple[str, ...]:
    """Return deterministic findings. Empty means the gate may proceed."""

    findings: list[str] = []
    findings.extend(_multiple_occupants(connection))
    findings.extend(_multiple_succeeded(connection))
    findings.extend(_duplicate_source_action_ids(connection))
    findings.extend(_succeeded_without_leave(connection))
    findings.extend(_contradictory_terminal_with_leave(connection))
    findings.extend(_nonterminal_with_leave(connection))
    findings.extend(_invalid_active_challenges(connection))
    findings.extend(_malformed_confirmation_timestamps(connection))
    findings.extend(_inconsistent_stored_business_keys(connection))
    return tuple(findings)


def assert_phase1a_invariants(connection: Connection) -> None:
    findings = collect_phase1a_invariant_violations(connection)
    if findings:
        raise Phase1AInvariantError(findings)


def _rows(connection: Connection, sql: str, **params: object) -> list:
    return list(connection.execute(text(sql), params).mappings())


def _multiple_occupants(connection: Connection) -> list[str]:
    rows = _rows(
        connection,
        f"""
        SELECT business_request_key, count(*) AS occupant_count
        FROM action_revisions
        WHERE {occupancy_where_sql()}
        GROUP BY business_request_key
        HAVING count(*) > 1
        """,
    )
    return [
        f"multiple transitional occupants for business_request_key={row['business_request_key']}"
        for row in rows
    ]


def _multiple_succeeded(connection: Connection) -> list[str]:
    rows = _rows(
        connection,
        """
        SELECT business_request_key, count(*) AS succeeded_count
        FROM action_revisions
        WHERE state = :state
        GROUP BY business_request_key
        HAVING count(*) > 1
        """,
        state=WorkflowState.SUCCEEDED.value,
    )
    return [
        f"multiple SUCCEEDED actions for business_request_key={row['business_request_key']}"
        for row in rows
    ]


def _duplicate_source_action_ids(connection: Connection) -> list[str]:
    rows = _rows(
        connection,
        """
        SELECT source_action_id, count(*) AS leave_count
        FROM leave_requests
        GROUP BY source_action_id
        HAVING count(*) > 1
        """,
    )
    return [f"duplicate leave_requests.source_action_id={row['source_action_id']}" for row in rows]


def _succeeded_without_leave(connection: Connection) -> list[str]:
    rows = _rows(
        connection,
        """
        SELECT ar.action_id
        FROM action_revisions ar
        WHERE ar.state = :state
          AND NOT EXISTS (
                SELECT 1 FROM leave_requests lr
                WHERE lr.source_action_id = ar.action_id
                   OR lr.business_request_key = ar.business_request_key
          )
        """,
        state=WorkflowState.SUCCEEDED.value,
    )
    return [
        f"SUCCEEDED action {row['action_id']} has no valid corresponding leave result"
        for row in rows
    ]


def _contradictory_terminal_with_leave(connection: Connection) -> list[str]:
    findings: list[str] = []
    for state in CONTRADICTORY_TERMINAL_WITH_LEAVE:
        rows = _rows(
            connection,
            """
            SELECT ar.action_id
            FROM action_revisions ar
            WHERE ar.state = :state
              AND EXISTS (
                    SELECT 1 FROM leave_requests lr
                    WHERE lr.source_action_id = ar.action_id
                       OR lr.business_request_key = ar.business_request_key
              )
            """,
            state=state,
        )
        findings.extend(
            f"{state} action {row['action_id']} has a committed business result" for row in rows
        )
    return findings


def _nonterminal_with_leave(connection: Connection) -> list[str]:
    findings: list[str] = []
    for state in NONTERMINAL_CONFIRMATION_STATES:
        source_rows = _rows(
            connection,
            """
            SELECT ar.action_id
            FROM action_revisions ar
            WHERE ar.state = :state
              AND EXISTS (
                    SELECT 1 FROM leave_requests lr
                    WHERE lr.source_action_id = ar.action_id
              )
            """,
            state=state,
        )
        findings.extend(
            f"{state} action {row['action_id']} has a source-linked committed leave"
            for row in source_rows
        )
        key_rows = _rows(
            connection,
            """
            SELECT ar.action_id
            FROM action_revisions ar
            WHERE ar.state = :state
              AND EXISTS (
                    SELECT 1 FROM leave_requests lr
                    WHERE lr.business_request_key = ar.business_request_key
              )
            """,
            state=state,
        )
        findings.extend(
            f"{state} action {row['action_id']} has a same-business-key committed leave"
            for row in key_rows
        )
    return findings


def _invalid_active_challenges(connection: Connection) -> list[str]:
    rows = _rows(
        connection,
        """
        SELECT c.challenge_id, ar.action_id, ar.state
        FROM confirmation_challenges c
        JOIN action_revisions ar
          ON ar.action_id = c.action_id AND ar.revision = c.revision
        WHERE c.status = :active
          AND ar.state <> :awaiting
        """,
        active=ChallengeStatus.ACTIVE.value,
        awaiting=WorkflowState.AWAITING_CONFIRMATION.value,
    )
    return [
        (
            f"active challenge {row['challenge_id']} attached to "
            f"{row['state']} action {row['action_id']}"
        )
        for row in rows
    ]


def _malformed_confirmation_timestamps(connection: Connection) -> list[str]:
    rows = _rows(
        connection,
        """
        SELECT action_id
        FROM action_revisions
        WHERE state = :confirmed
          AND (confirmed_at IS NULL OR confirmed_expires_at IS NULL)
        """,
        confirmed=WorkflowState.CONFIRMED.value,
    )
    return [
        f"CONFIRMED action {row['action_id']} has null confirmed_at or confirmed_expires_at"
        for row in rows
    ]


def _inconsistent_stored_business_keys(connection: Connection) -> list[str]:
    rows = _rows(
        connection,
        """
        SELECT ar.action_id, ar.business_request_key, ar.draft_payload,
               aw.owner_employee_id, aw.action_type
        FROM action_revisions ar
        JOIN action_workflows aw ON aw.action_id = ar.action_id
        """,
    )
    findings: list[str] = []
    for row in rows:
        payload = row["draft_payload"]
        if not isinstance(payload, dict):
            findings.append(f"action {row['action_id']} stored draft payload is not an object")
            continue
        try:
            draft = reconstruct_canonical_draft(payload)
        except (WorkflowIntegrityError, TypeError, ValueError):
            findings.append(
                f"action {row['action_id']} stored business key cannot be recomputed from draft"
            )
            continue
        expected = business_request_key(
            employee_id=row["owner_employee_id"],
            leave_type=draft.leave_type,
            start_date=draft.start_date,
            end_date=draft.end_date,
        )
        if expected != row["business_request_key"]:
            findings.append(
                f"action {row['action_id']} stored business_request_key is inconsistent "
                "with trusted owner/draft"
            )
        if row["action_type"] != ActionType.SUBMIT_ANNUAL_LEAVE.value:
            findings.append(f"action {row['action_id']} has unexpected action_type")
        if draft.leave_type != LeaveType.ANNUAL.value:
            findings.append(f"action {row['action_id']} draft leave_type is not annual")
    return findings
