from aiogram.types import User as TelegramApiUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from booking_bot.db.models import TelegramUser


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
) -> None:
    user.phone = phone
    await session.flush()
