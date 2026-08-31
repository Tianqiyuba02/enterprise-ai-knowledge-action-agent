from pathlib import Path

from app.workflow.cutover import MAINTENANCE_CUTOVER_PROCEDURE

ROOT = Path(__file__).resolve().parents[2]
CUTOVER_SOURCE = (ROOT / "src" / "app" / "workflow" / "cutover.py").read_text(encoding="utf-8")
MIGRATION_SOURCE = (ROOT / "migrations" / "versions" / "0005_v4_execution_cutover.py").read_text(
    encoding="utf-8"
)


def test_maintenance_cutover_procedure_is_explicit() -> None:
    assert MAINTENANCE_CUTOVER_PROCEDURE == (
        "stop ALL old application / worker processes",
        "prevent automatic restart",
        "confirm no old application execution transaction remains",
        "enter maintenance/no-write cutover window",
        "run authoritative invariant scan + normalization + 0005 migration",
        "start ONLY the new binary",
    )


def test_cutover_does_not_treat_sql_text_as_process_quiescence() -> None:
    assert "FROM pg_stat_activity" not in CUTOVER_SOURCE
    assert "query ILIKE" not in CUTOVER_SOURCE
    assert "_LEGACY_ACTIVITY_MARKERS" not in CUTOVER_SOURCE
    assert "assert_legacy_execution_quiesced" not in CUTOVER_SOURCE
    assert "FROM pg_stat_activity" not in MIGRATION_SOURCE
    assert "Maintenance-mode only" in MIGRATION_SOURCE
