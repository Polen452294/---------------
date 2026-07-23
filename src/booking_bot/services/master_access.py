import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from booking_bot.db.models import (
    BusinessMember,
    Master,
    MasterInvite,
    NotificationPreference,
    TelegramUser,
)
from booking_bot.domain.enums import MemberRole


class MasterAccessError(RuntimeError):
    pass


class InvalidMasterInviteError(MasterAccessError):
    pass


class MasterAlreadyLinkedError(MasterAccessError):
    pass


@dataclass(frozen=True, slots=True)
class CreatedMasterInvite:
    token: str
    expires_at: datetime
    master_id: UUID
    master_name: str


def _hash_invite_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def create_master_invite(
    session: AsyncSession,
    *,
    business_id: UUID,
    master_id: UUID,
    ttl: timedelta = timedelta(hours=24),
    now: datetime | None = None,
) -> CreatedMasterInvite:
    now = now or datetime.now(UTC)
    master = await session.scalar(
        select(Master).where(
            Master.id == master_id,
            Master.business_id == business_id,
            Master.is_active.is_(True),
        )
    )
    if master is None:
        raise MasterAccessError("Master not found")
    if master.user_id is not None:
        raise MasterAlreadyLinkedError("Master is already linked")

    token = secrets.token_urlsafe(24)
    invite = MasterInvite(
        business_id=business_id,
        master_id=master.id,
        code_hash=_hash_invite_token(token),
        expires_at=now + ttl,
    )
    session.add(invite)
    await session.flush()
    return CreatedMasterInvite(
        token=token,
        expires_at=invite.expires_at,
        master_id=master.id,
        master_name=master.display_name,
    )


async def redeem_master_invite(
    session: AsyncSession,
    *,
    business_id: UUID,
    token: str,
    user: TelegramUser,
    now: datetime | None = None,
) -> Master:
    now = now or datetime.now(UTC)
    invite = await session.scalar(
        select(MasterInvite)
        .where(
            MasterInvite.business_id == business_id,
            MasterInvite.code_hash == _hash_invite_token(token),
        )
        .with_for_update()
    )
    if invite is None or invite.used_at is not None or invite.expires_at <= now:
        raise InvalidMasterInviteError("Invite is invalid or expired")

    master = await session.get(Master, invite.master_id)
    if master is None or not master.is_active:
        raise InvalidMasterInviteError("Master is not available")
    if master.user_id is not None and master.user_id != user.id:
        raise MasterAlreadyLinkedError("Master is already linked to another user")

    master.user_id = user.id
    invite.used_at = now
    membership = await session.scalar(
        select(BusinessMember).where(
            BusinessMember.business_id == business_id,
            BusinessMember.user_id == user.id,
        )
    )
    if membership is None:
        session.add(
            BusinessMember(
                business_id=business_id,
                user_id=user.id,
                role=MemberRole.MASTER.value,
            )
        )
    else:
        membership.role = MemberRole.MASTER.value
        membership.is_active = True

    preference = await session.scalar(
        select(NotificationPreference).where(
            NotificationPreference.business_id == business_id,
            NotificationPreference.user_id == user.id,
        )
    )
    if preference is None:
        session.add(
            NotificationPreference(
                business_id=business_id,
                user_id=user.id,
                settings={"master_new_appointment": True},
            )
        )
    await session.flush()
    return master


async def get_master_for_user(
    session: AsyncSession,
    *,
    business_id: UUID,
    user_id: UUID,
) -> Master | None:
    return await session.scalar(
        select(Master).where(
            Master.business_id == business_id,
            Master.user_id == user_id,
            Master.is_active.is_(True),
        )
    )
