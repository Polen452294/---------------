from datetime import date, time
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKey, Integer, String, Time
from sqlalchemy.orm import Mapped, mapped_column

from booking_bot.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from booking_bot.domain.enums import ScheduleExceptionKind


class WorkingRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "working_rules"
    __table_args__ = (
        CheckConstraint("weekday >= 0 AND weekday <= 6", name="weekday_range"),
        CheckConstraint("end_time > start_time", name="working_interval_positive"),
    )

    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    master_id: Mapped[UUID] = mapped_column(
        ForeignKey("masters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    location_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("locations.id", ondelete="SET NULL"), index=True
    )
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_until: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ScheduleException(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "schedule_exceptions"
    __table_args__ = (
        CheckConstraint(
            "start_time IS NULL OR end_time IS NULL OR end_time > start_time",
            name="exception_interval_positive",
        ),
    )

    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    master_id: Mapped[UUID] = mapped_column(
        ForeignKey("masters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    location_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("locations.id", ondelete="SET NULL"), index=True
    )
    exception_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(
        String(24), nullable=False, default=ScheduleExceptionKind.DAY_OFF.value
    )
    start_time: Mapped[time | None] = mapped_column(Time)
    end_time: Mapped[time | None] = mapped_column(Time)
    reason: Mapped[str | None] = mapped_column(String(255))
