import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import escape
from uuid import UUID
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from booking_bot.config import Settings
from booking_bot.db.models import (
    Appointment,
    Business,
    CalendarEntry,
    Location,
    Master,
    NotificationJob,
    NotificationPreference,
    TelegramUser,
)
from booking_bot.db.session import async_session_factory
from booking_bot.domain.enums import NotificationJobState
from booking_bot.services.reminder_settings import get_client_reminder_settings
from booking_bot.specialist_config import get_specialist_template


class NotificationDeliveryError(RuntimeError):
    pass


class UnsupportedNotificationError(NotificationDeliveryError):
    pass


MASTER_NOTIFICATION_KINDS = {
    "master_new_appointment",
    "master_appointment_cancelled_by_client",
    "master_appointment_rescheduled_by_client",
}


@dataclass(frozen=True, slots=True)
class DeliveryPayload:
    chat_id: int
    text: str


async def master_notifications_enabled(
    session: AsyncSession,
    *,
    business_id: UUID,
    user_id: UUID,
) -> bool:
    preference = await session.scalar(
        select(NotificationPreference).where(
            NotificationPreference.business_id == business_id,
            NotificationPreference.user_id == user_id,
        )
    )
    if preference is None:
        return True
    return bool(preference.settings.get("master_new_appointment", True))


async def toggle_master_notifications(
    session: AsyncSession,
    *,
    business_id: UUID,
    user_id: UUID,
) -> bool:
    preference = await session.scalar(
        select(NotificationPreference).where(
            NotificationPreference.business_id == business_id,
            NotificationPreference.user_id == user_id,
        )
    )
    if preference is None:
        preference = NotificationPreference(
            business_id=business_id,
            user_id=user_id,
            settings={"master_new_appointment": False},
        )
        session.add(preference)
        await session.flush()
        return False
    settings = dict(preference.settings)
    enabled = not bool(settings.get("master_new_appointment", True))
    settings["master_new_appointment"] = enabled
    preference.settings = settings
    await session.flush()
    return enabled


class NotificationDeliveryService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def run_once(
        self,
        bot: Bot,
        *,
        business_id: UUID,
        now: datetime | None = None,
    ) -> int:
        now = now or datetime.now(UTC)
        job_ids = await self._claim_jobs(business_id=business_id, now=now)
        for job_id in job_ids:
            await self._deliver_job(bot, job_id=job_id, now=now)
        return len(job_ids)

    async def run_forever(self, bot: Bot, *, business_id: UUID) -> None:
        while True:
            processed = await self.run_once(bot, business_id=business_id)
            if processed == 0:
                await asyncio.sleep(self._settings.notification_poll_interval_seconds)

    async def _claim_jobs(self, *, business_id: UUID, now: datetime) -> list[UUID]:
        stale_before = now - timedelta(minutes=5)
        async with async_session_factory() as session:
            await session.execute(
                update(NotificationJob)
                .where(
                    NotificationJob.business_id == business_id,
                    NotificationJob.state == NotificationJobState.PROCESSING.value,
                    NotificationJob.updated_at < stale_before,
                )
                .values(
                    state=NotificationJobState.PENDING.value,
                    scheduled_for=now,
                    last_error="Recovered stale processing job",
                    updated_at=now,
                )
            )
            jobs = list(
                (
                    await session.scalars(
                        select(NotificationJob)
                        .where(
                            NotificationJob.business_id == business_id,
                            NotificationJob.state == NotificationJobState.PENDING.value,
                            NotificationJob.scheduled_for <= now,
                        )
                        .order_by(NotificationJob.scheduled_for)
                        .with_for_update(skip_locked=True)
                        .limit(self._settings.notification_batch_size)
                    )
                ).all()
            )
            for job in jobs:
                job.state = NotificationJobState.PROCESSING.value
                job.attempt_count += 1
                job.updated_at = now
            await session.commit()
            return [job.id for job in jobs]

    async def _deliver_job(self, bot: Bot, *, job_id: UUID, now: datetime) -> None:
        async with async_session_factory() as session:
            job = await session.get(NotificationJob, job_id)
            if job is None or job.state != NotificationJobState.PROCESSING.value:
                return
            try:
                if not await self._is_enabled(session, job):
                    job.state = NotificationJobState.CANCELLED.value
                    job.last_error = "Disabled by recipient preference"
                    await session.commit()
                    return
                payload = await self._build_payload(session, job)
                await bot.send_message(chat_id=payload.chat_id, text=payload.text)
            except TelegramForbiddenError as exc:
                job.state = NotificationJobState.FAILED.value
                job.last_error = str(exc)[:2000]
            except UnsupportedNotificationError as exc:
                job.state = NotificationJobState.FAILED.value
                job.last_error = str(exc)[:2000]
            except Exception as exc:
                job.last_error = str(exc)[:2000]
                if job.attempt_count >= self._settings.notification_max_attempts:
                    job.state = NotificationJobState.FAILED.value
                else:
                    delays = (15, 60, 300, 900, 3600)
                    delay = delays[min(job.attempt_count - 1, len(delays) - 1)]
                    job.state = NotificationJobState.PENDING.value
                    job.scheduled_for = now + timedelta(seconds=delay)
            else:
                job.state = NotificationJobState.SENT.value
                job.sent_at = datetime.now(UTC)
                job.last_error = None
            job.updated_at = datetime.now(UTC)
            await session.commit()

    async def _is_enabled(self, session: AsyncSession, job: NotificationJob) -> bool:
        if job.appointment_id is not None and (
            job.kind.startswith("client_reminder_")
            or job.kind in {"master_new_appointment", "client_schedule_changed"}
        ):
            appointment = await session.get(Appointment, job.appointment_id)
            if appointment is None or appointment.status in {
                "cancelled_by_client",
                "cancelled_by_master",
            }:
                return False
            if job.kind.startswith("client_reminder_"):
                entry = await session.get(CalendarEntry, appointment.calendar_entry_id)
                master = await session.get(Master, entry.master_id) if entry is not None else None
                settings = await get_client_reminder_settings(
                    session,
                    business_id=job.business_id,
                    master_user_id=master.user_id if master is not None else None,
                )
                return {
                    "client_reminder_7d": settings.seven_days,
                    "client_reminder_3d": settings.three_days,
                    "client_reminder_day_of": settings.day_of,
                }.get(job.kind, True)
        if job.kind not in MASTER_NOTIFICATION_KINDS:
            return True
        return await master_notifications_enabled(
            session,
            business_id=job.business_id,
            user_id=job.recipient_user_id,
        )

    async def _build_payload(
        self,
        session: AsyncSession,
        job: NotificationJob,
    ) -> DeliveryPayload:
        recipient = await session.get(TelegramUser, job.recipient_user_id)
        business = await session.get(Business, job.business_id)
        appointment = (
            await session.get(Appointment, job.appointment_id)
            if job.appointment_id is not None
            else None
        )
        if (
            recipient is None
            or recipient.telegram_user_id is None
            or business is None
            or appointment is None
        ):
            raise UnsupportedNotificationError("Notification context is incomplete")

        entry = await session.get(CalendarEntry, appointment.calendar_entry_id)
        master = await session.get(Master, entry.master_id) if entry is not None else None
        location = (
            await session.get(Location, entry.location_id)
            if entry is not None and entry.location_id is not None
            else None
        )
        if master is None:
            raise UnsupportedNotificationError("Appointment master is missing")
        timezone = ZoneInfo(master.timezone or business.timezone)
        local_start = appointment.service_starts_at.astimezone(timezone)
        location_text = f"\nАдрес: <b>{escape(location.name)}</b>" if location else ""

        if job.kind == "master_new_appointment":
            client_name = escape(appointment.client_name_snapshot or "Клиент")
            phone = escape(appointment.client_phone_snapshot or "не указан")
            text = (
                "🔔 <b>Новая запись</b>\n\n"
                f"Услуга: <b>{escape(appointment.service_name_snapshot)}</b>\n"
                f"Дата и время: <b>{local_start:%d.%m.%Y %H:%M}</b>\n"
                f"Клиент: <b>{client_name}</b>\n"
                f"Телефон: <code>{phone}</code>"
                f"{location_text}"
            )
        elif job.kind == "master_appointment_cancelled_by_client":
            client_name = escape(appointment.client_name_snapshot or "Клиент")
            phone = escape(appointment.client_phone_snapshot or "не указан")
            text = (
                "❌ <b>Клиент отменил запись</b>\n\n"
                f"Услуга: <b>{escape(appointment.service_name_snapshot)}</b>\n"
                f"Дата и время: <b>{local_start:%d.%m.%Y %H:%M}</b>\n"
                f"Клиент: <b>{client_name}</b>\n"
                f"Телефон: <code>{phone}</code>"
                f"{location_text}"
            )
        elif job.kind == "master_appointment_rescheduled_by_client":
            client_name = escape(appointment.client_name_snapshot or "Клиент")
            phone = escape(appointment.client_phone_snapshot or "не указан")
            text = (
                "🔄 <b>Клиент перенёс запись</b>\n\n"
                f"Услуга: <b>{escape(appointment.service_name_snapshot)}</b>\n"
                f"Новое время: <b>{local_start:%d.%m.%Y %H:%M}</b>\n"
                f"Клиент: <b>{client_name}</b>\n"
                f"Телефон: <code>{phone}</code>"
                f"{location_text}"
            )
        elif job.kind.startswith("client_reminder_"):
            text = (
                get_specialist_template().text(
                    "reminder_title",
                    "⏰ <b>Напоминание о записи</b>",
                    specialist_name=escape(master.display_name),
                )
                + "\n\n"
                f"Услуга: <b>{escape(appointment.service_name_snapshot)}</b>\n"
                f"Дата и время: <b>{local_start:%d.%m.%Y %H:%M}</b>"
                f"{location_text}"
            )
        elif job.kind == "client_appointment_cancelled":
            text = (
                get_specialist_template().text(
                    "appointment_cancelled",
                    "Запись отменена специалистом.",
                    specialist_name=escape(master.display_name),
                )
                + "\n\n"
                f"Услуга: <b>{escape(appointment.service_name_snapshot)}</b>\n"
                f"Дата и время: <b>{local_start:%d.%m.%Y %H:%M}</b>\n\n"
                "Для выбора нового времени откройте /start."
            )
        elif job.kind == "client_appointment_confirmed":
            text = (
                get_specialist_template().text(
                    "appointment_confirmed",
                    "✅ <b>Запись подтверждена</b>",
                    specialist_name=escape(master.display_name),
                )
                + "\n\n"
                f"Услуга: <b>{escape(appointment.service_name_snapshot)}</b>\n"
                f"Дата и время: <b>{local_start:%d.%m.%Y %H:%M}</b>"
                f"{location_text}"
            )
        elif job.kind == "client_appointment_rescheduled":
            text = (
                get_specialist_template().text(
                    "appointment_rescheduled",
                    "🔄 <b>Специалист перенёс запись</b>",
                    specialist_name=escape(master.display_name),
                )
                + "\n\n"
                f"Услуга: <b>{escape(appointment.service_name_snapshot)}</b>\n"
                f"Новое время: <b>{local_start:%d.%m.%Y %H:%M}</b>"
                f"{location_text}\n\n"
                "Если новое время не подходит, свяжитесь со специалистом."
            )
        elif job.kind == "client_schedule_changed":
            text = (
                "🗓 <b>Изменилось расписание специалиста</b>\n\n"
                f"У специалиста <b>{escape(master.display_name)}</b> изменился рабочий график.\n"
                "Ваша запись остаётся в силе:\n\n"
                f"Услуга: <b>{escape(appointment.service_name_snapshot)}</b>\n"
                f"Дата и время: <b>{local_start:%d.%m.%Y %H:%M}</b>"
                f"{location_text}\n\n"
                "Если время записи изменится или запись будет отменена, "
                "вы получите отдельное уведомление."
            )
        else:
            raise UnsupportedNotificationError(f"Unsupported notification kind: {job.kind}")
        return DeliveryPayload(chat_id=recipient.telegram_user_id, text=text)
