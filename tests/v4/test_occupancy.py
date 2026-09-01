from app.workflow.occupancy import (
    FINAL_OCCUPANCY_STATES,
    FINAL_OCCUPANCY_UNIQUE_INDEX,
    OCCUPANCY_UNIQUE_INDEX,
    TRANSITIONAL_OCCUPANCY_STATES,
    final_occupancy_where_sql,
    occupancy_where_sql,
)


def test_occupancy_predicate_is_final_three_state() -> None:
    clause = occupancy_where_sql()
    for state in TRANSITIONAL_OCCUPANCY_STATES:
        assert f"'{state}'" in clause
    assert "EXECUTING" not in clause
    assert "UNKNOWN_OUTCOME" not in clause
    assert "RECONCILING" not in clause
    assert "EXPIRED" not in clause
    assert OCCUPANCY_UNIQUE_INDEX != FINAL_OCCUPANCY_UNIQUE_INDEX


def test_final_occupancy_predicate_is_three_state() -> None:
    clause = final_occupancy_where_sql()
    for state in FINAL_OCCUPANCY_STATES:
        assert f"'{state}'" in clause
    assert "EXECUTING" not in clause
    assert "UNKNOWN_OUTCOME" not in clause
    assert "RECONCILING" not in clause
    assert "EXPIRED" not in clause
