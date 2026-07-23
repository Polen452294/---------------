from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from booking_bot.config import Settings
from booking_bot.db.models import (
    Business,
    CalendarEntry,
    Master,
    MasterService,
    ScheduleException,
    Service,
    SlotHold,
    WorkingRule,
)
from booking_bot.domain.enums import (
    CalendarEntryState,
    HoldStatus,
    ScheduleExceptionKind,
)
from booking_bot.services.slot_engine import TimeInterval, generate_slots


class BookingConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BookableSlot:
    service_start: datetime
    service_end: datetime
    occupied_start: datetime
    occupied_end: datetime
    location_id: UUID | None


@dataclass(frozen=True, slots=True)
class ServiceBookingConfig:
    service: Service
    duration: timedelta
    buffer_before: timedelta
    buffer_after: timedelta


class AvailabilityService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def get_service_config(
        self,
        session: AsyncSession,
        *,
        business_id: UUID,
        master_id: UUID,
        service_id: UUID,
    ) -> ServiceBookingConfig:
        row = (
            await session.execute(
                select(Service, MasterService)
                .join(
                    MasterService,
                    (MasterService.service_id == Service.id)
                    & (MasterService.master_id == master_id),
                )
                .where(
                    Service.id == service_id,
                    Service.business_id == business_id,
                    Service.is_active.is_(True),
                    MasterService.business_id == business_id,
                    MasterService.is_active.is_(True),
                )
            )
        ).one_or_none()
        if row is None:
            raise BookingConfigurationError("Service is not available for this master")

        service, master_service = row
        duration_minutes = master_service.duration_override_minutes or service.duration_minutes
        return ServiceBookingConfig(
            service=service,
            duration=timedelta(minutes=duration_minutes),
            buffer_before=timedelta(minutes=service.buffer_before_minutes),
            buffer_after=timedelta(minutes=service.buffer_after_minutes),
        )

    async def list_slots(
        self,
        session: AsyncSession,
        *,
        business_id: UUID,
        master_id: UUID,
        service_id: UUID,
        local_date: date,
        now: datetime | None = None,
    ) -> list[BookableSlot]:
        now = now or datetime.now(UTC)
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")

        business = await session.get(Business, business_id)
        master = await session.get(Master, master_id)
        if (
            business is None
            or not business.is_active
            or master is None
            or not master.is_active
            or master.business_id != business_id
        ):
            raise BookingConfigurationError("Business or master is not available")

        try:
            timezone = ZoneInfo(master.timezone or business.timezone)
        except ZoneInfoNotFoundError as exc:
            raise BookingConfigurationError("Business timezone is invalid") from exc

        local_today = now.astimezone(timezone).date()
        if local_date < local_today:
            return []
        if local_date > local_today + timedelta(days=self._settings.booking_horizon_days):
            return []

        await self._expire_stale_holds(session, now)
        service_config = await self.get_service_config(
            session,
            business_id=business_id,
            master_id=master_id,
            service_id=service_id,
        )

        rules = list(
            (
                await session.scalars(
                    select(WorkingRule).where(
                        WorkingRule.business_id == business_id,
                        WorkingRule.master_id == master_id,
                        WorkingRule.weekday == local_date.weekday(),
                        WorkingRule.is_active.is_(True),
                        or_(
                            WorkingRule.valid_from.is_(None),
                            WorkingRule.valid_from <= local_date,
                        ),
                        or_(
                            WorkingRule.valid_until.is_(None),
                            WorkingRule.valid_until >= local_date,
                        ),
                    )
                )
            ).all()
        )
        exceptions = list(
            (
                await session.scalars(
                    select(ScheduleException).where(
                        ScheduleException.business_id == business_id,
                        ScheduleException.master_id == master_id,
                        ScheduleException.exception_date == local_date,
                    )
                )
            ).all()
        )
        if any(item.kind == ScheduleExceptionKind.DAY_OFF.value for item in exceptions):
            return []

        custom_hours = [
            item for item in exceptions if item.kind == ScheduleExceptionKind.CUSTOM_HOURS.value
        ]
        extra_hours = [
            item for item in exceptions if item.kind == ScheduleExceptionKind.EXTRA_DAY.value
        ]

        windows: list[tuple[TimeInterval, UUID | None]] = []
        if custom_hours:
            windows.extend(self._exception_windows(custom_hours, local_date, timezone))
        else:
            windows.extend(
                (
                    TimeInterval(
                        datetime.combine(local_date, rule.start_time, timezone),
                        datetime.combine(local_date, rule.end_time, timezone),
                    ),
                    rule.location_id,
                )
                for rule in rules
            )
        windows.extend(self._exception_windows(extra_hours, local_date, timezone))
        if not windows:
            return []

        local_day_start = datetime.combine(local_date, datetime.min.time(), timezone)
        local_day_end = local_day_start + timedelta(days=1)
        busy_entries = list(
            (
                await session.scalars(
                    select(CalendarEntry).where(
                        CalendarEntry.business_id == business_id,
                        CalendarEntry.master_id == master_id,
                        CalendarEntry.state == CalendarEntryState.ACTIVE.value,
                        CalendarEntry.starts_at < local_day_end.astimezone(UTC),
                        CalendarEntry.ends_at > local_day_start.astimezone(UTC),
                    )
                )
            ).all()
        )
        busy = [TimeInterval(item.starts_at, item.ends_at) for item in busy_entries]
        earliest_start = now + timedelta(hours=self._settings.booking_min_lead_hours)

        found: dict[tuple[datetime, UUID | None], BookableSlot] = {}
        for window, location_id in windows:
            window_utc = TimeInterval(window.start.astimezone(UTC), window.end.astimezone(UTC))
            service_slots = generate_slots(
                [window_utc],
                busy,
                duration=service_config.duration,
                step=timedelta(minutes=30),
                buffer_before=service_config.buffer_before,
                buffer_after=service_config.buffer_after,
                earliest_start=earliest_start,
            )
            for slot in service_slots:
                bookable = BookableSlot(
                    service_start=slot.start,
                    service_end=slot.end,
                    occupied_start=slot.start - service_config.buffer_before,
                    occupied_end=slot.end + service_config.buffer_after,
                    location_id=location_id,
                )
                found[(bookable.service_start, location_id)] = bookable

        return sorted(found.values(), key=lambda item: item.service_start)

    @staticmethod
    def _exception_windows(
        exceptions: list[ScheduleException],
        local_date: date,
        timezone: ZoneInfo,
    ) -> list[tuple[TimeInterval, UUID | None]]:
        return [
            (
                TimeInterval(
                    datetime.combine(local_date, item.start_time, timezone),
                    datetime.combine(local_date, item.end_time, timezone),
                ),
                item.location_id,
            )
            for item in exceptions
            if item.start_time is not None and item.end_time is not None
        ]

    @staticmethod
    async def _expire_stale_holds(
        session: AsyncSession,
        now: datetime,
    ) -> None:
        stale_entry_ids = select(SlotHold.calendar_entry_id).where(
            SlotHold.status == HoldStatus.ACTIVE.value,
            SlotHold.expires_at <= now,
        )
        await session.execute(
            update(CalendarEntry)
            .where(
                CalendarEntry.id.in_(stale_entry_ids),
                CalendarEntry.state == CalendarEntryState.ACTIVE.value,
            )
            .values(state=CalendarEntryState.EXPIRED.value)
        )
        await session.execute(
            update(SlotHold)
            .where(
                SlotHold.status == HoldStatus.ACTIVE.value,
                SlotHold.expires_at <= now,
            )
            .values(status=HoldStatus.EXPIRED.value)
        )
