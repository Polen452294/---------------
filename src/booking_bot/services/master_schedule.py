from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from booking_bot.db.models import (
    Appointment,
    AppointmentHistory,
    Business,
    CalendarEntry,
    Location,
    Master,
    NotificationJob,
    ScheduleException,
    TimeBlock,
    WorkingRule,
)
from booking_bot.domain.enums import (
    AppointmentStatus,
    CalendarEntryKind,
    CalendarEntryState,
    NotificationJobState,
    ScheduleExceptionKind,
)


class MasterScheduleError(RuntimeError):
    pass


class AppointmentNotFoundError(MasterScheduleError):
    pass


class InvalidAppointmentTransitionError(MasterScheduleError):
    pass


class ScheduleConflictError(MasterScheduleError):
    pass


@dataclass(frozen=True, slots=True)
class MasterAppointment:
    appointment_id: UUID
    service_name: str
    client_name: str | None
    client_phone: str | None
    location_name: str | None
    local_start: datetime
    local_end: datetime
    status: str


@dataclass(frozen=True, slots=True)
class WeeklyWorkingInterval:
    weekday: int
    start_time: time
    end_time: time


@dataclass(frozen=True, slots=True)
class MasterTimeBlock:
    block_id: UUID
    local_start: datetime
    local_end: datetime
    reason: str | None


class MasterScheduleService:
    async def list_appointments(
        self,
        session: AsyncSession,
        *,
        business_id: UUID,
        master: Master,
        start_date: date,
        days: int,
    ) -> list[MasterAppointment]:
        business = await session.get(Business, business_id)
        if business is None:
            raise MasterScheduleError("Business not found")
        timezone = ZoneInfo(master.timezone or business.timezone)
        start = datetime.combine(start_date, time.min, timezone).astimezone(UTC)
        end = datetime.combine(start_date + timedelta(days=days), time.min, timezone).astimezone(
            UTC
        )
        rows = (
            await session.execute(
                select(Appointment, CalendarEntry, Location)
                .join(CalendarEntry, CalendarEntry.id == Appointment.calendar_entry_id)
                .outerjoin(Location, Location.id == CalendarEntry.location_id)
                .where(
                    Appointment.business_id == business_id,
                    CalendarEntry.master_id == master.id,
                    Appointment.service_starts_at >= start,
                    Appointment.service_starts_at < end,
                    Appointment.status.notin_(
                        [
                            AppointmentStatus.CANCELLED_BY_CLIENT.value,
                            AppointmentStatus.CANCELLED_BY_MASTER.value,
                        ]
                    ),
                )
                .order_by(Appointment.service_starts_at)
            )
        ).all()
        return [
            self._appointment_summary(appointment, location, timezone)
            for appointment, _entry, location in rows
        ]

    async def get_appointment(
        self,
        session: AsyncSession,
        *,
        business_id: UUID,
        master: Master,
        appointment_id: UUID,
    ) -> MasterAppointment:
        business = await session.get(Business, business_id)
        if business is None:
            raise AppointmentNotFoundError
        row = (
            await session.execute(
                select(Appointment, CalendarEntry, Location)
                .join(CalendarEntry, CalendarEntry.id == Appointment.calendar_entry_id)
                .outerjoin(Location, Location.id == CalendarEntry.location_id)
                .where(
                    Appointment.id == appointment_id,
                    Appointment.business_id == business_id,
                    CalendarEntry.master_id == master.id,
                )
            )
        ).one_or_none()
        if row is None:
            raise AppointmentNotFoundError
        appointment, _entry, location = row
        return self._appointment_summary(
            appointment,
            location,
            ZoneInfo(master.timezone or business.timezone),
        )

    async def change_appointment_status(
        self,
        session: AsyncSession,
        *,
        business_id: UUID,
        master: Master,
        appointment_id: UUID,
        actor_user_id: UUID,
        new_status: str,
        now: datetime | None = None,
    ) -> MasterAppointment:
        now = now or datetime.now(UTC)
        appointment = await session.scalar(
            select(Appointment)
            .join(CalendarEntry, CalendarEntry.id == Appointment.calendar_entry_id)
            .where(
                Appointment.id == appointment_id,
                Appointment.business_id == business_id,
                CalendarEntry.master_id == master.id,
            )
            .with_for_update()
        )
        if appointment is None:
            raise AppointmentNotFoundError

        allowed = {
            AppointmentStatus.PENDING_APPROVAL.value: {
                AppointmentStatus.CONFIRMED.value,
                AppointmentStatus.CANCELLED_BY_MASTER.value,
            },
            AppointmentStatus.CONFIRMED.value: {
                AppointmentStatus.COMPLETED.value,
                AppointmentStatus.NO_SHOW.value,
                AppointmentStatus.CANCELLED_BY_MASTER.value,
            },
        }
        if new_status not in allowed.get(appointment.status, set()):
            raise InvalidAppointmentTransitionError(
                f"Cannot change {appointment.status} to {new_status}"
            )
        if (
            new_status
            in {
                AppointmentStatus.COMPLETED.value,
                AppointmentStatus.NO_SHOW.value,
            }
            and appointment.service_starts_at > now
        ):
            raise InvalidAppointmentTransitionError(
                "A future appointment cannot be completed or marked as no-show"
            )

        previous_status = appointment.status
        appointment.status = new_status
        appointment.lock_version += 1
        entry = await session.get(CalendarEntry, appointment.calendar_entry_id)
        if new_status == AppointmentStatus.CONFIRMED.value:
            session.add(
                NotificationJob(
                    business_id=business_id,
                    appointment_id=appointment.id,
                    recipient_user_id=appointment.client_id,
                    kind="client_appointment_confirmed",
                    scheduled_for=now,
                    state=NotificationJobState.PENDING.value,
                )
            )
        elif new_status == AppointmentStatus.CANCELLED_BY_MASTER.value:
            if entry is not None:
                entry.state = CalendarEntryState.RELEASED.value
            await session.execute(
                update(NotificationJob)
                .where(
                    NotificationJob.appointment_id == appointment.id,
                    NotificationJob.state == NotificationJobState.PENDING.value,
                )
                .values(state=NotificationJobState.CANCELLED.value)
            )
            session.add(
                NotificationJob(
                    business_id=business_id,
                    appointment_id=appointment.id,
                    recipient_user_id=appointment.client_id,
                    kind="client_appointment_cancelled",
                    scheduled_for=now,
                    state=NotificationJobState.PENDING.value,
                )
            )

        session.add(
            AppointmentHistory(
                business_id=business_id,
                appointment_id=appointment.id,
                actor_user_id=actor_user_id,
                event_type="status_changed_by_master",
                from_status=previous_status,
                to_status=new_status,
            )
        )
        await session.flush()
        return await self.get_appointment(
            session,
            business_id=business_id,
            master=master,
            appointment_id=appointment.id,
        )

    async def list_weekly_rules(
        self,
        session: AsyncSession,
        *,
        business_id: UUID,
        master_id: UUID,
    ) -> list[WeeklyWorkingInterval]:
        rules = list(
            (
                await session.scalars(
                    select(WorkingRule)
                    .where(
                        WorkingRule.business_id == business_id,
                        WorkingRule.master_id == master_id,
                        WorkingRule.is_active.is_(True),
                    )
                    .order_by(WorkingRule.weekday, WorkingRule.start_time)
                )
            ).all()
        )
        return [
            WeeklyWorkingInterval(
                weekday=rule.weekday,
                start_time=rule.start_time,
                end_time=rule.end_time,
            )
            for rule in rules
        ]

    async def set_weekday_hours(
        self,
        session: AsyncSession,
        *,
        business_id: UUID,
        master_id: UUID,
        weekday: int,
        start_time: time | None,
        end_time: time | None,
    ) -> None:
        if weekday not in range(7):
            raise ValueError("weekday must be between 0 and 6")
        if (start_time is None) != (end_time is None):
            raise ValueError("start_time and end_time must both be provided")
        if start_time is not None and end_time is not None and end_time <= start_time:
            raise ValueError("end_time must be after start_time")

        await session.execute(
            delete(WorkingRule).where(
                WorkingRule.business_id == business_id,
                WorkingRule.master_id == master_id,
                WorkingRule.weekday == weekday,
            )
        )
        if start_time is not None and end_time is not None:
            location_id = await session.scalar(
                select(Location.id)
                .where(
                    Location.business_id == business_id,
                    Location.is_active.is_(True),
                )
                .order_by(Location.created_at)
                .limit(1)
            )
            session.add(
                WorkingRule(
                    business_id=business_id,
                    master_id=master_id,
                    location_id=location_id,
                    weekday=weekday,
                    start_time=start_time,
                    end_time=end_time,
                )
            )
        await session.flush()

    async def toggle_day_off(
        self,
        session: AsyncSession,
        *,
        business_id: UUID,
        master_id: UUID,
        local_date: date,
    ) -> bool:
        existing = list(
            (
                await session.scalars(
                    select(ScheduleException).where(
                        ScheduleException.business_id == business_id,
                        ScheduleException.master_id == master_id,
                        ScheduleException.exception_date == local_date,
                        ScheduleException.kind == ScheduleExceptionKind.DAY_OFF.value,
                    )
                )
            ).all()
        )
        if existing:
            for item in existing:
                await session.delete(item)
            await session.flush()
            return False
        session.add(
            ScheduleException(
                business_id=business_id,
                master_id=master_id,
                exception_date=local_date,
                kind=ScheduleExceptionKind.DAY_OFF.value,
            )
        )
        await session.flush()
        return True

    async def list_day_off_dates(
        self,
        session: AsyncSession,
        *,
        business_id: UUID,
        master_id: UUID,
        start_date: date,
        days: int,
    ) -> set[date]:
        end_date = start_date + timedelta(days=days)
        return set(
            (
                await session.scalars(
                    select(ScheduleException.exception_date).where(
                        ScheduleException.business_id == business_id,
                        ScheduleException.master_id == master_id,
                        ScheduleException.kind == ScheduleExceptionKind.DAY_OFF.value,
                        ScheduleException.exception_date >= start_date,
                        ScheduleException.exception_date < end_date,
                    )
                )
            ).all()
        )

    async def add_extra_day(
        self,
        session: AsyncSession,
        *,
        business_id: UUID,
        master_id: UUID,
        local_date: date,
        start_time: time,
        end_time: time,
    ) -> None:
        if end_time <= start_time:
            raise ValueError("end_time must be after start_time")
        await session.execute(
            delete(ScheduleException).where(
                ScheduleException.business_id == business_id,
                ScheduleException.master_id == master_id,
                ScheduleException.exception_date == local_date,
                ScheduleException.kind.in_(
                    [
                        ScheduleExceptionKind.DAY_OFF.value,
                        ScheduleExceptionKind.EXTRA_DAY.value,
                    ]
                ),
            )
        )
        location_id = await session.scalar(
            select(Location.id)
            .where(
                Location.business_id == business_id,
                Location.is_active.is_(True),
            )
            .order_by(Location.created_at)
            .limit(1)
        )
        session.add(
            ScheduleException(
                business_id=business_id,
                master_id=master_id,
                location_id=location_id,
                exception_date=local_date,
                kind=ScheduleExceptionKind.EXTRA_DAY.value,
                start_time=start_time,
                end_time=end_time,
            )
        )
        await session.flush()

    async def create_time_block(
        self,
        session: AsyncSession,
        *,
        business_id: UUID,
        master_id: UUID,
        starts_at: datetime,
        ends_at: datetime,
        reason: str | None,
        now: datetime | None = None,
    ) -> TimeBlock:
        now = now or datetime.now(UTC)
        if starts_at.tzinfo is None or ends_at.tzinfo is None:
            raise ValueError("Block times must be timezone-aware")
        if starts_at <= now or ends_at <= starts_at:
            raise ValueError("Block interval must be in the future")
        entry = CalendarEntry(
            business_id=business_id,
            master_id=master_id,
            starts_at=starts_at.astimezone(UTC),
            ends_at=ends_at.astimezone(UTC),
            kind=CalendarEntryKind.BLOCK.value,
            state=CalendarEntryState.ACTIVE.value,
        )
        block = TimeBlock(
            business_id=business_id,
            calendar_entry_id=entry.id,
            reason=reason,
        )
        try:
            async with session.begin_nested():
                session.add(entry)
                await session.flush()
                block.calendar_entry_id = entry.id
                session.add(block)
                await session.flush()
        except IntegrityError as exc:
            raise ScheduleConflictError("The block overlaps an active calendar entry") from exc
        return block

    async def list_time_blocks(
        self,
        session: AsyncSession,
        *,
        business_id: UUID,
        master: Master,
        now: datetime | None = None,
    ) -> list[MasterTimeBlock]:
        now = now or datetime.now(UTC)
        business = await session.get(Business, business_id)
        if business is None:
            raise MasterScheduleError("Business not found")
        timezone = ZoneInfo(master.timezone or business.timezone)
        rows = (
            await session.execute(
                select(TimeBlock, CalendarEntry)
                .join(CalendarEntry, CalendarEntry.id == TimeBlock.calendar_entry_id)
                .where(
                    TimeBlock.business_id == business_id,
                    CalendarEntry.master_id == master.id,
                    CalendarEntry.state == CalendarEntryState.ACTIVE.value,
                    CalendarEntry.ends_at > now,
                )
                .order_by(CalendarEntry.starts_at)
            )
        ).all()
        return [
            MasterTimeBlock(
                block_id=block.id,
                local_start=entry.starts_at.astimezone(timezone),
                local_end=entry.ends_at.astimezone(timezone),
                reason=block.reason,
            )
            for block, entry in rows
        ]

    async def release_time_block(
        self,
        session: AsyncSession,
        *,
        business_id: UUID,
        master_id: UUID,
        block_id: UUID,
    ) -> None:
        entry = await session.scalar(
            select(CalendarEntry)
            .join(TimeBlock, TimeBlock.calendar_entry_id == CalendarEntry.id)
            .where(
                TimeBlock.id == block_id,
                TimeBlock.business_id == business_id,
                CalendarEntry.master_id == master_id,
                CalendarEntry.state == CalendarEntryState.ACTIVE.value,
            )
            .with_for_update()
        )
        if entry is not None:
            entry.state = CalendarEntryState.RELEASED.value
            await session.flush()

    @staticmethod
    def _appointment_summary(
        appointment: Appointment,
        location: Location | None,
        timezone: ZoneInfo,
    ) -> MasterAppointment:
        return MasterAppointment(
            appointment_id=appointment.id,
            service_name=appointment.service_name_snapshot,
            client_name=appointment.client_name_snapshot,
            client_phone=appointment.client_phone_snapshot,
            location_name=location.name if location else None,
            local_start=appointment.service_starts_at.astimezone(timezone),
            local_end=appointment.service_ends_at.astimezone(timezone),
            status=appointment.status,
        )
