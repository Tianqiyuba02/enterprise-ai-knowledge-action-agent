from app.workflow.occupancy import (
    FINAL_OCCUPANCY_UNIQUE_INDEX,
    OCCUPANCY_UNIQUE_INDEX,
    TRANSITIONAL_OCCUPANCY_STATES,
    occupancy_where_sql,
)


def test_transitional_occupancy_predicate_includes_legacy_unresolved() -> None:
    clause = occupancy_where_sql()
    for state in TRANSITIONAL_OCCUPANCY_STATES:
        assert f"'{state}'" in clause
    assert "EXPIRED" not in clause
    assert OCCUPANCY_UNIQUE_INDEX != FINAL_OCCUPANCY_UNIQUE_INDEX
