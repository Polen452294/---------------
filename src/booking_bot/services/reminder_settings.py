from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from booking_bot.db.models import NotificationPreference


@dataclass(frozen=True, slots=True)
class ClientReminderSettings:
    seven_days: bool = True
    three_days: bool = True
    day_of: bool = True
    day_of_hour: int = 9


async def get_client_reminder_settings(
    session: AsyncSession,
    *,
    business_id: UUID,
    master_user_id: UUID | None,
) -> ClientReminderSettings:
    if master_user_id is None:
        return ClientReminderSettings()
    preference = await session.scalar(
        select(NotificationPreference).where(
            NotificationPreference.business_id == business_id,
            NotificationPreference.user_id == master_user_id,
        )
    )
    values = preference.settings if preference is not None else {}
    hour = values.get("client_reminder_day_of_hour", 9)
    if not isinstance(hour, int) or not 0 <= hour <= 23:
        hour = 9
    return ClientReminderSettings(
        seven_days=bool(values.get("client_reminder_7d", True)),
        three_days=bool(values.get("client_reminder_3d", True)),
        day_of=bool(values.get("client_reminder_day_of", True)),
        day_of_hour=hour,
    )


async def update_client_reminder_settings(
    session: AsyncSession,
    *,
    business_id: UUID,
    master_user_id: UUID,
    toggle: str | None = None,
    day_of_hour: int | None = None,
) -> ClientReminderSettings:
    if toggle not in {None, "7d", "3d", "day"}:
        raise ValueError("Unknown reminder toggle")
    if day_of_hour is not None and not 0 <= day_of_hour <= 23:
        raise ValueError("Reminder hour must be between 0 and 23")

    preference = await session.scalar(
        select(NotificationPreference).where(
            NotificationPreference.business_id == business_id,
            NotificationPreference.user_id == master_user_id,
        )
    )
    if preference is None:
        preference = NotificationPreference(
            business_id=business_id,
            user_id=master_user_id,
            settings={},
        )
        session.add(preference)
    values = dict(preference.settings)
    if toggle is not None:
        key = {
            "7d": "client_reminder_7d",
            "3d": "client_reminder_3d",
            "day": "client_reminder_day_of",
        }[toggle]
        values[key] = not bool(values.get(key, True))
    if day_of_hour is not None:
        values["client_reminder_day_of_hour"] = day_of_hour
    preference.settings = values
    await session.flush()
    return await get_client_reminder_settings(
        session,
        business_id=business_id,
        master_user_id=master_user_id,
    )
