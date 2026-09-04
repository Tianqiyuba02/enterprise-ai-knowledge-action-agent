"""Add M3 public-demo operations and extended trusted calendar.

Revision ID: 0008_v5_m3_public_demo
Revises: 0007_v5_m2_multi_domain_actions
Create Date: 2026-09-03
"""

from collections.abc import Sequence
from datetime import date
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_v5_m3_public_demo"
down_revision: str | Sequence[str] | None = "0007_v5_m2_multi_domain_actions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CALENDAR_VERSION = "AU-VIC-2026-2028-v1"
HOLIDAYS = (
    (date(2026, 1, 1), "New Year's Day"),
    (date(2026, 1, 26), "Australia Day"),
    (date(2026, 3, 9), "Labour Day"),
    (date(2026, 4, 3), "Good Friday"),
    (date(2026, 4, 4), "Saturday before Easter Sunday"),
    (date(2026, 4, 5), "Easter Sunday"),
    (date(2026, 4, 6), "Easter Monday"),
    (date(2026, 4, 25), "ANZAC Day"),
    (date(2026, 6, 8), "King's Birthday"),
    (date(2026, 9, 25), "Friday before the AFL Grand Final"),
    (date(2026, 11, 3), "Melbourne Cup"),
    (date(2026, 12, 25), "Christmas Day"),
    (date(2026, 12, 26), "Boxing Day"),
    (date(2026, 12, 28), "Additional public holiday for Boxing Day"),
    (date(2027, 1, 1), "New Year's Day"),
    (date(2027, 1, 26), "Australia Day"),
    (date(2027, 3, 8), "Labour Day"),
    (date(2027, 3, 26), "Good Friday"),
    (date(2027, 3, 27), "Saturday before Easter Sunday"),
    (date(2027, 3, 28), "Easter Sunday"),
    (date(2027, 3, 29), "Easter Monday"),
    (date(2027, 4, 25), "ANZAC Day"),
    (date(2027, 6, 14), "King's Birthday"),
    (date(2027, 11, 2), "Melbourne Cup"),
    (date(2027, 12, 25), "Christmas Day"),
    (date(2027, 12, 26), "Boxing Day"),
    (date(2027, 12, 27), "Additional public holiday for Christmas Day"),
    (date(2027, 12, 28), "Additional public holiday for Boxing Day"),
    (date(2028, 1, 1), "New Year's Day"),
    (date(2028, 1, 3), "Additional public holiday for New Year's Day"),
    (date(2028, 1, 26), "Australia Day"),
    (date(2028, 3, 13), "Labour Day"),
    (date(2028, 4, 14), "Good Friday"),
    (date(2028, 4, 15), "Saturday before Easter Sunday"),
    (date(2028, 4, 16), "Easter Sunday"),
    (date(2028, 4, 17), "Easter Monday"),
    (date(2028, 4, 25), "ANZAC Day"),
    (date(2028, 6, 12), "King's Birthday"),
    (date(2028, 11, 7), "Melbourne Cup"),
    (date(2028, 12, 25), "Christmas Day"),
    (date(2028, 12, 26), "Boxing Day"),
)


def upgrade() -> None:
    op.create_table(
        "demo_runtime_state",
        sa.Column("singleton_id", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "maintenance_mode", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("maintenance_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_successful_reset_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("singleton_id = 1", name="ck_demo_runtime_state_singleton"),
        sa.PrimaryKeyConstraint("singleton_id"),
    )
    op.create_table(
        "demo_usage_buckets",
        sa.Column("bucket_date", sa.Date(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("scope_key", sa.Text(), nullable=False),
        sa.Column("metric", sa.Text(), nullable=False),
        sa.Column("usage_count", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("token_count", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("usage_count >= 0", name="ck_demo_usage_count_nonnegative"),
        sa.CheckConstraint("token_count >= 0", name="ck_demo_token_count_nonnegative"),
        sa.CheckConstraint("btrim(scope) <> ''", name="ck_demo_usage_scope_nonempty"),
        sa.CheckConstraint("btrim(scope_key) <> ''", name="ck_demo_usage_scope_key_nonempty"),
        sa.CheckConstraint("btrim(metric) <> ''", name="ck_demo_usage_metric_nonempty"),
        sa.PrimaryKeyConstraint("bucket_date", "scope", "scope_key", "metric"),
    )
    op.execute(sa.text("INSERT INTO demo_runtime_state (singleton_id) VALUES (1)"))
    holidays = sa.table(
        "public_holidays",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("jurisdiction", sa.Text()),
        sa.column("holiday_date", sa.Date()),
        sa.column("holiday_name", sa.Text()),
        sa.column("calendar_version", sa.Text()),
    )
    op.bulk_insert(
        holidays,
        [
            {
                "id": uuid4(),
                "jurisdiction": "AU-VIC",
                "holiday_date": holiday_date,
                "holiday_name": name,
                "calendar_version": CALENDAR_VERSION,
            }
            for holiday_date, name in HOLIDAYS
        ],
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM public_holidays WHERE calendar_version = :version").bindparams(
            version=CALENDAR_VERSION
        )
    )
    op.drop_table("demo_usage_buckets")
    op.drop_table("demo_runtime_state")
