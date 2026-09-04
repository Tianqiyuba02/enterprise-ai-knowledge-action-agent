"""M3 public-demo operational state. Never stores identity authority or prompt text."""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DemoRuntimeState(Base):
    __tablename__ = "demo_runtime_state"
    __table_args__ = (CheckConstraint("singleton_id = 1", name="ck_demo_runtime_state_singleton"),)

    singleton_id: Mapped[int] = mapped_column(Integer, primary_key=True, server_default=text("1"))
    maintenance_mode: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    maintenance_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_successful_reset_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    worker_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DemoUsageBucket(Base):
    __tablename__ = "demo_usage_buckets"
    __table_args__ = (
        CheckConstraint("usage_count >= 0", name="ck_demo_usage_count_nonnegative"),
        CheckConstraint("token_count >= 0", name="ck_demo_token_count_nonnegative"),
        CheckConstraint("btrim(scope) <> ''", name="ck_demo_usage_scope_nonempty"),
        CheckConstraint("btrim(scope_key) <> ''", name="ck_demo_usage_scope_key_nonempty"),
        CheckConstraint("btrim(metric) <> ''", name="ck_demo_usage_metric_nonempty"),
    )

    bucket_date: Mapped[date] = mapped_column(Date, primary_key=True)
    scope: Mapped[str] = mapped_column(Text, primary_key=True)
    scope_key: Mapped[str] = mapped_column(Text, primary_key=True)
    metric: Mapped[str] = mapped_column(Text, primary_key=True)
    usage_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    token_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
