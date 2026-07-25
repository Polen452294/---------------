import re
from uuid import UUID

from aiogram.types import User as TelegramApiUser
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from booking_bot.db.models import Appointment, NotificationJob, SlotHold, TelegramUser


def normalize_phone(raw_phone: str) -> str | None:
    digits = re.sub(r"\D", "", raw_phone)
    if not 10 <= len(digits) <= 15:
        return None
    if len(digits) == 11 and digits.startswith("8"):
        digits = f"7{digits[1:]}"
    return f"+{digits}"


async def get_or_create_telegram_user(
    session: AsyncSession,
    telegram_user: TelegramApiUser,
) -> TelegramUser:
    user = await session.scalar(
        select(TelegramUser).where(TelegramUser.telegram_user_id == telegram_user.id)
    )
    if user is None:
        user = TelegramUser(telegram_user_id=telegram_user.id)
        session.add(user)

    user.username = telegram_user.username
    user.first_name = telegram_user.first_name
    user.last_name = telegram_user.last_name
    user.locale = telegram_user.language_code
    await session.flush()
    return user


async def set_user_phone(
    session: AsyncSession,
    user: TelegramUser,
    phone: str,
) -> list[UUID]:
    normalized_phone = normalize_phone(phone)
    if normalized_phone is None:
        raise ValueError("Phone must contain 10-15 digits")

    user.phone = normalized_phone
    manual_clients = list(
        (
            await session.scalars(
                select(TelegramUser).where(
                    TelegramUser.id != user.id,
                    TelegramUser.telegram_user_id.is_(None),
                    TelegramUser.phone.is_not(None),
                )
            )
        ).all()
    )
    manual_client_ids = [
        manual_client.id
        for manual_client in manual_clients
        if normalize_phone(manual_client.phone or "") == normalized_phone
    ]
    if not manual_client_ids:
        await session.flush()
        return []

    appointment_ids = list(
        (
            await session.scalars(
                select(Appointment.id).where(Appointment.client_id.in_(manual_client_ids))
            )
        ).all()
    )
    await session.execute(
        update(Appointment)
        .where(Appointment.client_id.in_(manual_client_ids))
        .values(client_id=user.id)
    )
    await session.execute(
        update(SlotHold)
        .where(SlotHold.client_id.in_(manual_client_ids))
        .values(client_id=user.id)
    )
    await session.execute(
        update(NotificationJob)
        .where(NotificationJob.recipient_user_id.in_(manual_client_ids))
        .values(recipient_user_id=user.id)
    )
    await session.execute(delete(TelegramUser).where(TelegramUser.id.in_(manual_client_ids)))
    await session.flush()
    return appointment_ids
