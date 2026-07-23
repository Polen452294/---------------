from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import delete

from booking_bot.config import Settings
from booking_bot.db.models import (
    Appointment,
    Business,
    CalendarEntry,
    Master,
    NotificationJob,
    Service,
    TelegramUser,
)
from booking_bot.db.session import async_session_factory
from booking_bot.domain.enums import (
    AppointmentStatus,
    CalendarEntryKind,
    CalendarEntryState,
    NotificationJobState,
)
from booking_bot.services.notification_delivery import NotificationDeliveryService


@pytest.mark.integration
async def test_worker_marks_job_sent_only_after_telegram_accepts_message() -> None:
    now = datetime(2026, 7, 23, 8, tzinfo=UTC)
    async with async_session_factory() as session:
        suffix = uuid4().hex[:12]
        business = Business(
            slug=f"delivery-{suffix}",
            name="Delivery Test",
            timezone="Europe/Moscow",
        )
        client = TelegramUser(
            telegram_user_id=-(uuid4().int % 2_000_000_000),
            first_name="Иван",
            phone="+79990000000",
        )
        session.add_all([business, client])
        await session.flush()
        user_ids = [client.id]
        master = Master(business_id=business.id, display_name="Анна")
        service = Service(
            business_id=business.id,
            name="Консультация",
            duration_minutes=60,
        )
        session.add_all([master, service])
        await session.flush()
        starts_at = now + timedelta(days=3)
        entry = CalendarEntry(
            business_id=business.id,
            master_id=master.id,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=1),
            kind=CalendarEntryKind.APPOINTMENT.value,
            state=CalendarEntryState.ACTIVE.value,
        )
        session.add(entry)
        await session.flush()
        appointment = Appointment(
            business_id=business.id,
            calendar_entry_id=entry.id,
            service_id=service.id,
            client_id=client.id,
            status=AppointmentStatus.CONFIRMED.value,
            service_name_snapshot=service.name,
            service_starts_at=starts_at,
            service_ends_at=starts_at + timedelta(hours=1),
            duration_minutes=60,
            client_name_snapshot=client.first_name,
            client_phone_snapshot=client.phone,
        )
        session.add(appointment)
        await session.flush()
        job = NotificationJob(
            business_id=business.id,
            appointment_id=appointment.id,
            recipient_user_id=client.id,
            kind="client_reminder_3d",
            scheduled_for=now - timedelta(seconds=1),
        )
        session.add(job)
        await session.commit()
        business_id = business.id
        job_id = job.id

    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=None)
    processed = await NotificationDeliveryService(Settings(notification_batch_size=10)).run_once(
        bot, business_id=business_id, now=now
    )
    assert processed == 1
    bot.send_message.assert_awaited_once()

    async with async_session_factory() as session:
        delivered = await session.get(NotificationJob, job_id)
        assert delivered is not None
        assert delivered.state == NotificationJobState.SENT.value
        assert delivered.sent_at is not None

        await session.execute(delete(Business).where(Business.id == business_id))
        await session.flush()
        await session.execute(delete(TelegramUser).where(TelegramUser.id.in_(user_ids)))
        await session.commit()
