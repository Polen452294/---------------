from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from booking_bot.db.models import Business, Master, SpecialistProfile


class SpecialistNotConfiguredError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SpecialistContext:
    profile: SpecialistProfile
    business: Business
    master: Master

    @property
    def business_id(self):
        return self.business.id

    @property
    def master_id(self):
        return self.master.id


async def get_specialist_context(session: AsyncSession) -> SpecialistContext:
    row = (
        await session.execute(
            select(SpecialistProfile, Business, Master)
            .join(Business, Business.id == SpecialistProfile.business_id)
            .join(Master, Master.id == SpecialistProfile.master_id)
            .where(SpecialistProfile.id == 1)
        )
    ).one_or_none()
    if row is None:
        raise SpecialistNotConfiguredError("Run `booking-admin configure` before starting the bot")
    profile, business, master = row
    if not business.is_active or not master.is_active:
        raise SpecialistNotConfiguredError("The specialist profile is disabled")
    return SpecialistContext(profile=profile, business=business, master=master)
