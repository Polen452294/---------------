from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from booking_bot.db.models import Business, CalendarEntry, Master
from booking_bot.db.session import async_session_factory
from booking_bot.domain.enums import CalendarEntryKind, CalendarEntryState


@pytest.mark.integration
async def test_postgres_rejects_overlapping_active_entries() -> None:
    async with async_session_factory() as session:
        business = Business(slug="integration-overlap", name="Integration Test")
        session.add(business)
        await session.flush()
        master = Master(business_id=business.id, display_name="Test Master")
        session.add(master)
        await session.flush()

        starts_at = datetime(2026, 8, 3, 9, tzinfo=UTC)
        session.add(
            CalendarEntry(
                business_id=business.id,
                master_id=master.id,
                starts_at=starts_at,
                ends_at=starts_at + timedelta(hours=2),
                kind=CalendarEntryKind.APPOINTMENT.value,
                state=CalendarEntryState.ACTIVE.value,
            )
        )
        await session.flush()

        session.add(
            CalendarEntry(
                business_id=business.id,
                master_id=master.id,
                starts_at=starts_at + timedelta(hours=1),
                ends_at=starts_at + timedelta(hours=3),
                kind=CalendarEntryKind.HOLD.value,
                state=CalendarEntryState.ACTIVE.value,
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()
