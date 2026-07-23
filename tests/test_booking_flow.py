from datetime import UTC, date, datetime, time
from uuid import uuid4

import pytest
from sqlalchemy import select

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
    TelegramUser,
    WorkingRule,
)
from booking_bot.db.session import async_session_factory
from booking_bot.domain.enums import (
    AppointmentStatus,
    CalendarEntryKind,
    HoldStatus,
    NotificationJobState,
)
from booking_bot.services.availability import AvailabilityService
from booking_bot.services.bookings import BookingService


@pytest.mark.integration
async def test_client_can_hold_and_confirm_an_available_slot() -> None:
    settings = Settings(
        booking_horizon_days=60,
        booking_min_lead_hours=3,
        slot_hold_minutes=10,
    )
    now = datetime(2026, 7, 20, 6, tzinfo=UTC)
    booking_date = date(2026, 8, 3)

    async with async_session_factory() as session:
        suffix = uuid4().hex[:12]
        business = Business(
            slug=f"integration-booking-{suffix}",
            name="Integration Booking Studio",
            timezone="Europe/Moscow",
        )
        client = TelegramUser(
            telegram_user_id=-(uuid4().int % 2_000_000_000),
            first_name="Иван",
            phone="+79990000000",
        )
        master_user = TelegramUser(
            telegram_user_id=-(uuid4().int % 2_000_000_000),
            first_name="Анна",
        )
        session.add_all([business, client, master_user])
        await session.flush()

        location = Location(
            business_id=business.id,
            name="Студия",
            timezone="Europe/Moscow",
        )
        master = Master(
            business_id=business.id,
            user_id=master_user.id,
            display_name="Анна",
            timezone="Europe/Moscow",
        )
        service = Service(
            business_id=business.id,
            name="Консультация",
            duration_minutes=60,
            buffer_before_minutes=15,
            buffer_after_minutes=15,
            price_minor=200_000,
            currency="RUB",
        )
        session.add_all([location, master, service])
        await session.flush()
        session.add_all(
            [
                MasterService(
                    business_id=business.id,
                    master_id=master.id,
                    service_id=service.id,
                ),
                WorkingRule(
                    business_id=business.id,
                    master_id=master.id,
                    location_id=location.id,
                    weekday=booking_date.weekday(),
                    start_time=time(10),
                    end_time=time(19),
                ),
            ]
        )
        await session.flush()

        availability = AvailabilityService(settings)
        slots = await availability.list_slots(
            session,
            business_id=business.id,
            master_id=master.id,
            service_id=service.id,
            local_date=booking_date,
            now=now,
        )
        assert slots
        selected = slots[0]

        booking = BookingService(settings)
        hold = await booking.create_hold(
            session,
            business_id=business.id,
            master_id=master.id,
            service_id=service.id,
            client_id=client.id,
            service_start=selected.service_start,
            local_date=booking_date,
            now=now,
        )
        assert hold.status == HoldStatus.ACTIVE.value

        remaining_slots = await availability.list_slots(
            session,
            business_id=business.id,
            master_id=master.id,
            service_id=service.id,
            local_date=booking_date,
            now=now,
        )
        assert selected.service_start not in {slot.service_start for slot in remaining_slots}

        summary = await booking.confirm_hold(
            session,
            hold_id=hold.id,
            client_id=client.id,
            now=now,
        )
        assert summary.status == AppointmentStatus.CONFIRMED.value
        assert summary.local_start.date() == booking_date

        appointment = await session.scalar(
            select(Appointment).where(Appointment.id == summary.appointment_id)
        )
        assert appointment is not None
        assert appointment.client_phone_snapshot == client.phone
        assert appointment.service_starts_at == selected.service_start
        assert hold.status == HoldStatus.CONVERTED.value

        entry = await session.get(CalendarEntry, hold.calendar_entry_id)
        assert entry is not None
        assert entry.kind == CalendarEntryKind.APPOINTMENT.value

        history = await session.scalar(
            select(AppointmentHistory).where(AppointmentHistory.appointment_id == appointment.id)
        )
        assert history is not None
        assert history.event_type == "created_from_hold"

        jobs = list(
            (
                await session.scalars(
                    select(NotificationJob).where(NotificationJob.appointment_id == appointment.id)
                )
            ).all()
        )
        assert {job.kind for job in jobs} == {
            "client_reminder_7d",
            "client_reminder_3d",
            "client_reminder_day_of",
            "master_new_appointment",
        }
        assert all(job.state == NotificationJobState.PENDING.value for job in jobs)

        await session.rollback()
