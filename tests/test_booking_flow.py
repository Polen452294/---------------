from datetime import UTC, date, datetime, time, timedelta
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
    CalendarEntryState,
    HoldStatus,
    NotificationJobState,
)
from booking_bot.services.availability import AvailabilityService
from booking_bot.services.bookings import (
    AppointmentChangeNotAllowedError,
    BookingService,
)


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


async def _create_confirmed_appointment(
    session,
    *,
    settings: Settings,
    now: datetime,
    booking_date: date,
):
    suffix = uuid4().hex[:12]
    business = Business(
        slug=f"integration-change-{suffix}",
        name="Integration Change Studio",
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
    selected = (
        await availability.list_slots(
            session,
            business_id=business.id,
            master_id=master.id,
            service_id=service.id,
            local_date=booking_date,
            now=now,
        )
    )[0]
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
    summary = await booking.confirm_hold(
        session,
        hold_id=hold.id,
        client_id=client.id,
        now=now,
    )
    appointment = await session.get(Appointment, summary.appointment_id)
    assert appointment is not None
    return business, client, master, service, appointment, hold, booking


@pytest.mark.integration
async def test_client_can_cancel_appointment_before_cutoff() -> None:
    settings = Settings(cancellation_cutoff_hours=24)
    now = datetime(2026, 7, 20, 6, tzinfo=UTC)
    booking_date = date(2026, 8, 3)
    async with async_session_factory() as session:
        (
            business,
            client,
            master,
            _service,
            appointment,
            hold,
            booking,
        ) = await _create_confirmed_appointment(
            session,
            settings=settings,
            now=now,
            booking_date=booking_date,
        )
        summary = await booking.cancel_appointment(
            session,
            business_id=business.id,
            client_id=client.id,
            appointment_id=appointment.id,
            now=now,
        )
        assert summary.status == AppointmentStatus.CANCELLED_BY_CLIENT.value
        assert not summary.can_change
        old_entry = await session.get(CalendarEntry, hold.calendar_entry_id)
        assert old_entry is not None
        assert old_entry.state == CalendarEntryState.RELEASED.value
        jobs = list(
            (
                await session.scalars(
                    select(NotificationJob).where(NotificationJob.appointment_id == appointment.id)
                )
            ).all()
        )
        cancellation_jobs = [
            job for job in jobs if job.kind == "master_appointment_cancelled_by_client"
        ]
        assert len(cancellation_jobs) == 1
        assert cancellation_jobs[0].recipient_user_id == master.user_id
        assert cancellation_jobs[0].state == NotificationJobState.PENDING.value
        assert all(
            job.state == NotificationJobState.CANCELLED.value
            for job in jobs
            if job.kind != "master_appointment_cancelled_by_client"
        )
        history = await session.scalar(
            select(AppointmentHistory).where(
                AppointmentHistory.appointment_id == appointment.id,
                AppointmentHistory.event_type == "cancelled_by_client",
            )
        )
        assert history is not None
        await session.rollback()


@pytest.mark.integration
async def test_client_can_reschedule_appointment_and_reminders() -> None:
    settings = Settings(cancellation_cutoff_hours=24)
    now = datetime(2026, 7, 20, 6, tzinfo=UTC)
    booking_date = date(2026, 8, 3)
    new_date = date(2026, 8, 10)
    async with async_session_factory() as session:
        (
            business,
            client,
            master,
            service,
            appointment,
            old_hold,
            booking,
        ) = await _create_confirmed_appointment(
            session,
            settings=settings,
            now=now,
            booking_date=booking_date,
        )
        old_start = appointment.service_starts_at
        old_entry_id = appointment.calendar_entry_id
        selected = (
            await AvailabilityService(settings).list_slots(
                session,
                business_id=business.id,
                master_id=master.id,
                service_id=service.id,
                local_date=new_date,
                now=now,
            )
        )[0]
        new_hold = await booking.create_hold(
            session,
            business_id=business.id,
            master_id=master.id,
            service_id=service.id,
            client_id=client.id,
            service_start=selected.service_start,
            local_date=new_date,
            now=now,
        )
        summary = await booking.confirm_reschedule(
            session,
            business_id=business.id,
            client_id=client.id,
            appointment_id=appointment.id,
            hold_id=new_hold.id,
            now=now,
        )
        assert summary.local_start.date() == new_date
        assert appointment.service_starts_at == selected.service_start
        assert appointment.service_starts_at != old_start
        assert appointment.calendar_entry_id == new_hold.calendar_entry_id
        assert new_hold.status == HoldStatus.CONVERTED.value
        new_entry = await session.get(CalendarEntry, new_hold.calendar_entry_id)
        old_entry = await session.get(CalendarEntry, old_entry_id)
        assert new_entry is not None
        assert new_entry.kind == CalendarEntryKind.APPOINTMENT.value
        assert old_entry is not None
        assert old_entry.state == CalendarEntryState.RELEASED.value
        assert old_hold.status == HoldStatus.CONVERTED.value

        jobs = list(
            (
                await session.scalars(
                    select(NotificationJob).where(NotificationJob.appointment_id == appointment.id)
                )
            ).all()
        )
        assert any(
            job.kind == "master_appointment_rescheduled_by_client"
            and job.state == NotificationJobState.PENDING.value
            for job in jobs
        )
        assert {
            job.kind
            for job in jobs
            if job.kind.startswith("client_reminder_")
            and job.state == NotificationJobState.PENDING.value
        } == {
            "client_reminder_7d",
            "client_reminder_3d",
            "client_reminder_day_of",
        }
        assert any(
            job.kind.startswith("client_reminder_")
            and job.state == NotificationJobState.CANCELLED.value
            for job in jobs
        )
        history = await session.scalar(
            select(AppointmentHistory).where(
                AppointmentHistory.appointment_id == appointment.id,
                AppointmentHistory.event_type == "rescheduled_by_client",
            )
        )
        assert history is not None
        assert history.event_payload["old_starts_at"] == old_start.isoformat()
        assert history.event_payload["new_starts_at"] == selected.service_start.isoformat()
        await session.rollback()


@pytest.mark.integration
async def test_client_cannot_change_appointment_inside_cutoff() -> None:
    settings = Settings(cancellation_cutoff_hours=24)
    created_at = datetime(2026, 7, 20, 6, tzinfo=UTC)
    booking_date = date(2026, 8, 3)
    async with async_session_factory() as session:
        (
            business,
            client,
            _master,
            _service,
            appointment,
            _hold,
            booking,
        ) = await _create_confirmed_appointment(
            session,
            settings=settings,
            now=created_at,
            booking_date=booking_date,
        )
        inside_cutoff = appointment.service_starts_at - timedelta(hours=23)
        with pytest.raises(AppointmentChangeNotAllowedError):
            await booking.cancel_appointment(
                session,
                business_id=business.id,
                client_id=client.id,
                appointment_id=appointment.id,
                now=inside_cutoff,
            )
        assert appointment.status == AppointmentStatus.CONFIRMED.value
        await session.rollback()
