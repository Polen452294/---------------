from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from booking_bot.db.models import (
    Appointment,
    AppointmentHistory,
    Business,
    BusinessMember,
    CalendarEntry,
    Master,
    NotificationJob,
    NotificationPreference,
    Service,
    TelegramUser,
)
from booking_bot.db.session import async_session_factory
from booking_bot.domain.enums import (
    AppointmentStatus,
    CalendarEntryKind,
    CalendarEntryState,
    MemberRole,
    NotificationJobState,
)
from booking_bot.services.master_access import (
    InvalidMasterInviteError,
    create_master_invite,
    redeem_master_invite,
)
from booking_bot.services.master_schedule import MasterScheduleService


@pytest.mark.integration
async def test_master_invite_is_single_use_and_grants_master_role() -> None:
    async with async_session_factory() as session:
        suffix = uuid4().hex[:12]
        business = Business(slug=f"invite-{suffix}", name="Invite Test")
        user = TelegramUser(telegram_user_id=-(uuid4().int % 2_000_000_000))
        session.add_all([business, user])
        await session.flush()
        master = Master(business_id=business.id, display_name="Анна")
        session.add(master)
        await session.flush()

        invite = await create_master_invite(
            session,
            business_id=business.id,
            master_id=master.id,
            now=datetime(2026, 7, 23, 8, tzinfo=UTC),
        )
        linked_master = await redeem_master_invite(
            session,
            business_id=business.id,
            token=invite.token,
            user=user,
            now=datetime(2026, 7, 23, 8, 1, tzinfo=UTC),
        )
        assert linked_master.user_id == user.id

        membership = await session.scalar(
            select(BusinessMember).where(
                BusinessMember.business_id == business.id,
                BusinessMember.user_id == user.id,
            )
        )
        assert membership is not None
        assert membership.role == MemberRole.MASTER.value

        preference = await session.scalar(
            select(NotificationPreference).where(
                NotificationPreference.business_id == business.id,
                NotificationPreference.user_id == user.id,
            )
        )
        assert preference is not None
        assert preference.settings["master_new_appointment"] is True

        with pytest.raises(InvalidMasterInviteError):
            await redeem_master_invite(
                session,
                business_id=business.id,
                token=invite.token,
                user=user,
                now=datetime(2026, 7, 23, 8, 2, tzinfo=UTC),
            )
        await session.rollback()


@pytest.mark.integration
async def test_master_cancellation_releases_slot_and_notifies_client() -> None:
    async with async_session_factory() as session:
        suffix = uuid4().hex[:12]
        business = Business(slug=f"cancel-{suffix}", name="Cancellation Test")
        master_user = TelegramUser(telegram_user_id=-(uuid4().int % 2_000_000_000))
        client = TelegramUser(
            telegram_user_id=-(uuid4().int % 2_000_000_000),
            phone="+79990000000",
        )
        session.add_all([business, master_user, client])
        await session.flush()
        master = Master(
            business_id=business.id,
            user_id=master_user.id,
            display_name="Анна",
        )
        service = Service(
            business_id=business.id,
            name="Консультация",
            duration_minutes=60,
        )
        session.add_all([master, service])
        await session.flush()

        starts_at = datetime(2026, 8, 3, 7, tzinfo=UTC)
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
            client_phone_snapshot=client.phone,
        )
        session.add(appointment)
        await session.flush()
        reminder = NotificationJob(
            business_id=business.id,
            appointment_id=appointment.id,
            recipient_user_id=client.id,
            kind="client_reminder_3d",
            scheduled_for=starts_at - timedelta(days=3),
        )
        session.add(reminder)
        await session.flush()

        result = await MasterScheduleService().change_appointment_status(
            session,
            business_id=business.id,
            master=master,
            appointment_id=appointment.id,
            actor_user_id=master_user.id,
            new_status=AppointmentStatus.CANCELLED_BY_MASTER.value,
            now=datetime(2026, 7, 23, 8, tzinfo=UTC),
        )
        assert result.status == AppointmentStatus.CANCELLED_BY_MASTER.value
        assert entry.state == CalendarEntryState.RELEASED.value
        assert reminder.state == NotificationJobState.CANCELLED.value

        cancellation_job = await session.scalar(
            select(NotificationJob).where(
                NotificationJob.appointment_id == appointment.id,
                NotificationJob.kind == "client_appointment_cancelled",
            )
        )
        assert cancellation_job is not None
        assert cancellation_job.state == NotificationJobState.PENDING.value

        history = await session.scalar(
            select(AppointmentHistory).where(
                AppointmentHistory.appointment_id == appointment.id,
                AppointmentHistory.event_type == "status_changed_by_master",
            )
        )
        assert history is not None
        assert history.from_status == AppointmentStatus.CONFIRMED.value
        assert history.to_status == AppointmentStatus.CANCELLED_BY_MASTER.value
        await session.rollback()
