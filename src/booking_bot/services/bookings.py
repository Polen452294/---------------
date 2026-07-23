from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from booking_bot.config import Settings
from booking_bot.db.models import (
    Appointment,
    AppointmentHistory,
    Business,
    CalendarEntry,
    Location,
    Master,
    MasterService,
    NotificationJob,
    Service,
    SlotHold,
    TelegramUser,
)
from booking_bot.domain.enums import (
    AppointmentStatus,
    CalendarEntryKind,
    CalendarEntryState,
    HoldStatus,
    NotificationJobState,
)
from booking_bot.services.availability import AvailabilityService, BookableSlot


class SlotUnavailableError(RuntimeError):
    pass


class HoldNotFoundError(LookupError):
    pass


class HoldExpiredError(RuntimeError):
    pass


class ClientPhoneRequiredError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class HoldSummary:
    hold_id: UUID
    business_name: str
    service_name: str
    master_name: str
    location_name: str | None
    local_start: datetime
    local_end: datetime
    expires_at: datetime
    client_phone: str | None


@dataclass(frozen=True, slots=True)
class AppointmentSummary:
    appointment_id: UUID
    service_name: str
    master_name: str
    location_name: str | None
    local_start: datetime
    local_end: datetime
    status: str


class BookingService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._availability = AvailabilityService(settings)

    async def create_hold(
        self,
        session: AsyncSession,
        *,
        business_id: UUID,
        master_id: UUID,
        service_id: UUID,
        client_id: UUID,
        service_start: datetime,
        local_date: date,
        now: datetime | None = None,
    ) -> SlotHold:
        now = now or datetime.now(UTC)
        if service_start.tzinfo is None:
            raise ValueError("service_start must be timezone-aware")

        slots = await self._availability.list_slots(
            session,
            business_id=business_id,
            master_id=master_id,
            service_id=service_id,
            local_date=local_date,
            now=now,
        )
        selected = self._find_slot(slots, service_start.astimezone(UTC))
        if selected is None:
            raise SlotUnavailableError("The selected slot is no longer available")

        calendar_entry = CalendarEntry(
            business_id=business_id,
            master_id=master_id,
            location_id=selected.location_id,
            starts_at=selected.occupied_start,
            ends_at=selected.occupied_end,
            kind=CalendarEntryKind.HOLD.value,
            state=CalendarEntryState.ACTIVE.value,
        )
        hold = SlotHold(
            business_id=business_id,
            calendar_entry_id=calendar_entry.id,
            service_id=service_id,
            client_id=client_id,
            expires_at=now + timedelta(minutes=self._settings.slot_hold_minutes),
            service_starts_at=selected.service_start,
            service_ends_at=selected.service_end,
            status=HoldStatus.ACTIVE.value,
        )
        try:
            async with session.begin_nested():
                session.add(calendar_entry)
                await session.flush()
                hold.calendar_entry_id = calendar_entry.id
                session.add(hold)
                await session.flush()
        except IntegrityError as exc:
            raise SlotUnavailableError("The selected slot was just booked") from exc
        return hold

    async def get_hold_summary(
        self,
        session: AsyncSession,
        *,
        hold_id: UUID,
        client_id: UUID,
    ) -> HoldSummary:
        row = (
            await session.execute(
                select(SlotHold, CalendarEntry, Service, Master, Business, Location)
                .join(CalendarEntry, CalendarEntry.id == SlotHold.calendar_entry_id)
                .join(Service, Service.id == SlotHold.service_id)
                .join(Master, Master.id == CalendarEntry.master_id)
                .join(Business, Business.id == SlotHold.business_id)
                .outerjoin(Location, Location.id == CalendarEntry.location_id)
                .where(SlotHold.id == hold_id, SlotHold.client_id == client_id)
            )
        ).one_or_none()
        if row is None:
            raise HoldNotFoundError

        hold, _entry, service, master, business, location = row
        client = await session.get(TelegramUser, client_id)
        timezone = ZoneInfo(master.timezone or business.timezone)
        return HoldSummary(
            hold_id=hold.id,
            business_name=business.name,
            service_name=service.name,
            master_name=master.display_name,
            location_name=location.name if location else None,
            local_start=hold.service_starts_at.astimezone(timezone),
            local_end=hold.service_ends_at.astimezone(timezone),
            expires_at=hold.expires_at,
            client_phone=client.phone if client else None,
        )

    async def confirm_hold(
        self,
        session: AsyncSession,
        *,
        hold_id: UUID,
        client_id: UUID,
        now: datetime | None = None,
    ) -> AppointmentSummary:
        now = now or datetime.now(UTC)
        hold = await session.scalar(
            select(SlotHold)
            .where(SlotHold.id == hold_id, SlotHold.client_id == client_id)
            .with_for_update()
        )
        if hold is None:
            raise HoldNotFoundError

        entry = await session.get(CalendarEntry, hold.calendar_entry_id)
        if (
            hold.status != HoldStatus.ACTIVE.value
            or hold.expires_at <= now
            or entry is None
            or entry.state != CalendarEntryState.ACTIVE.value
        ):
            if entry is not None and entry.state == CalendarEntryState.ACTIVE.value:
                entry.state = CalendarEntryState.EXPIRED.value
            hold.status = HoldStatus.EXPIRED.value
            await session.flush()
            raise HoldExpiredError("The slot hold has expired")

        service = await session.get(Service, hold.service_id)
        master = await session.get(Master, entry.master_id)
        business = await session.get(Business, hold.business_id)
        client = await session.get(TelegramUser, client_id)
        location = (
            await session.get(Location, entry.location_id)
            if entry.location_id is not None
            else None
        )
        if service is None or master is None or business is None or client is None:
            raise HoldNotFoundError
        if not client.phone:
            raise ClientPhoneRequiredError("Client phone is required")

        master_service = await session.scalar(
            select(MasterService).where(
                MasterService.master_id == master.id,
                MasterService.service_id == service.id,
            )
        )
        price_minor = (
            master_service.price_override_minor
            if master_service and master_service.price_override_minor is not None
            else service.price_minor
        )
        appointment_status = (
            AppointmentStatus.PENDING_APPROVAL.value
            if service.requires_approval
            else AppointmentStatus.CONFIRMED.value
        )

        entry.kind = CalendarEntryKind.APPOINTMENT.value
        hold.status = HoldStatus.CONVERTED.value
        client_name = (
            " ".join(part for part in (client.first_name, client.last_name) if part) or None
        )
        appointment = Appointment(
            business_id=business.id,
            calendar_entry_id=entry.id,
            service_id=service.id,
            client_id=client.id,
            created_by_user_id=client.id,
            status=appointment_status,
            service_name_snapshot=service.name,
            service_starts_at=hold.service_starts_at,
            service_ends_at=hold.service_ends_at,
            duration_minutes=int(
                (hold.service_ends_at - hold.service_starts_at).total_seconds() // 60
            ),
            price_minor=price_minor,
            currency=service.currency,
            client_name_snapshot=client_name,
            client_phone_snapshot=client.phone,
        )
        session.add(appointment)
        await session.flush()
        session.add(
            AppointmentHistory(
                business_id=business.id,
                appointment_id=appointment.id,
                actor_user_id=client.id,
                event_type="created_from_hold",
                to_status=appointment_status,
            )
        )
        await self._schedule_notifications(
            session,
            appointment=appointment,
            client=client,
            master=master,
            business=business,
            now=now,
        )
        await session.flush()

        timezone = ZoneInfo(master.timezone or business.timezone)
        return AppointmentSummary(
            appointment_id=appointment.id,
            service_name=appointment.service_name_snapshot,
            master_name=master.display_name,
            location_name=location.name if location else None,
            local_start=appointment.service_starts_at.astimezone(timezone),
            local_end=appointment.service_ends_at.astimezone(timezone),
            status=appointment.status,
        )

    async def release_hold(
        self,
        session: AsyncSession,
        *,
        hold_id: UUID,
        client_id: UUID,
    ) -> None:
        hold = await session.scalar(
            select(SlotHold).where(
                SlotHold.id == hold_id,
                SlotHold.client_id == client_id,
                SlotHold.status == HoldStatus.ACTIVE.value,
            )
        )
        if hold is None:
            return
        entry = await session.get(CalendarEntry, hold.calendar_entry_id)
        hold.status = HoldStatus.RELEASED.value
        if entry is not None:
            entry.state = CalendarEntryState.RELEASED.value
        await session.flush()

    async def list_upcoming(
        self,
        session: AsyncSession,
        *,
        business_id: UUID,
        client_id: UUID,
        now: datetime | None = None,
    ) -> list[AppointmentSummary]:
        now = now or datetime.now(UTC)
        rows = (
            await session.execute(
                select(Appointment, CalendarEntry, Master, Business, Location)
                .join(CalendarEntry, CalendarEntry.id == Appointment.calendar_entry_id)
                .join(Master, Master.id == CalendarEntry.master_id)
                .join(Business, Business.id == Appointment.business_id)
                .outerjoin(Location, Location.id == CalendarEntry.location_id)
                .where(
                    Appointment.business_id == business_id,
                    Appointment.client_id == client_id,
                    Appointment.service_starts_at >= now,
                    Appointment.status.in_(
                        [
                            AppointmentStatus.PENDING_APPROVAL.value,
                            AppointmentStatus.PENDING_PAYMENT.value,
                            AppointmentStatus.CONFIRMED.value,
                        ]
                    ),
                )
                .order_by(Appointment.service_starts_at)
            )
        ).all()
        return [
            AppointmentSummary(
                appointment_id=appointment.id,
                service_name=appointment.service_name_snapshot,
                master_name=master.display_name,
                location_name=location.name if location else None,
                local_start=appointment.service_starts_at.astimezone(
                    ZoneInfo(master.timezone or business.timezone)
                ),
                local_end=appointment.service_ends_at.astimezone(
                    ZoneInfo(master.timezone or business.timezone)
                ),
                status=appointment.status,
            )
            for appointment, _entry, master, business, location in rows
        ]

    async def _schedule_notifications(
        self,
        session: AsyncSession,
        *,
        appointment: Appointment,
        client: TelegramUser,
        master: Master,
        business: Business,
        now: datetime,
    ) -> None:
        timezone = ZoneInfo(master.timezone or business.timezone)
        local_start = appointment.service_starts_at.astimezone(timezone)
        day_of = datetime.combine(local_start.date(), time(9), timezone).astimezone(UTC)
        reminders = [
            ("client_reminder_7d", appointment.service_starts_at - timedelta(days=7)),
            ("client_reminder_3d", appointment.service_starts_at - timedelta(days=3)),
            ("client_reminder_day_of", day_of),
        ]
        for kind, scheduled_for in reminders:
            if now < scheduled_for < appointment.service_starts_at:
                session.add(
                    NotificationJob(
                        business_id=business.id,
                        appointment_id=appointment.id,
                        recipient_user_id=client.id,
                        kind=kind,
                        scheduled_for=scheduled_for,
                        state=NotificationJobState.PENDING.value,
                    )
                )
        if master.user_id is not None:
            session.add(
                NotificationJob(
                    business_id=business.id,
                    appointment_id=appointment.id,
                    recipient_user_id=master.user_id,
                    kind="master_new_appointment",
                    scheduled_for=now,
                    state=NotificationJobState.PENDING.value,
                )
            )

    @staticmethod
    def _find_slot(
        slots: list[BookableSlot],
        service_start: datetime,
    ) -> BookableSlot | None:
        return next(
            (slot for slot in slots if slot.service_start == service_start),
            None,
        )
