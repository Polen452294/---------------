from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from booking_bot.services.slot_engine import TimeInterval, generate_slots

TZ = ZoneInfo("Europe/Moscow")


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 3, hour, minute, tzinfo=TZ)


def test_generates_slots_with_service_duration() -> None:
    slots = generate_slots(
        [TimeInterval(at(10), at(13))],
        [],
        duration=timedelta(hours=1),
        step=timedelta(minutes=30),
    )

    assert [slot.start for slot in slots] == [at(10), at(10, 30), at(11), at(11, 30), at(12)]


def test_removes_slots_that_overlap_busy_time_and_buffers() -> None:
    slots = generate_slots(
        [TimeInterval(at(10), at(14, 30))],
        [TimeInterval(at(12), at(13))],
        duration=timedelta(hours=1),
        step=timedelta(minutes=30),
        buffer_before=timedelta(minutes=15),
        buffer_after=timedelta(minutes=15),
    )

    assert [slot.start for slot in slots] == [at(10, 15), at(10, 45), at(13, 15)]


def test_applies_booking_boundaries() -> None:
    slots = generate_slots(
        [TimeInterval(at(9), at(15))],
        [],
        duration=timedelta(hours=1),
        step=timedelta(hours=1),
        earliest_start=at(11),
        latest_start=at(12),
    )

    assert [slot.start for slot in slots] == [at(11), at(12)]


def test_rejects_naive_datetimes() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        TimeInterval(datetime(2026, 8, 3, 10), datetime(2026, 8, 3, 11))
