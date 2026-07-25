from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from booking_bot.services.analytics import build_analytics_period


def test_previous_month_period_crosses_year_boundary() -> None:
    period = build_analytics_period(
        mode="previous_month",
        timezone=ZoneInfo("Europe/Moscow"),
        now=datetime(2026, 1, 15, 12, tzinfo=UTC),
    )

    assert period.local_start_date.isoformat() == "2025-12-01"
    assert period.local_end_date.isoformat() == "2026-01-01"
    assert period.starts_at == datetime(2025, 11, 30, 21, tzinfo=UTC)
    assert period.ends_at == datetime(2025, 12, 31, 21, tzinfo=UTC)


def test_seven_day_period_includes_today() -> None:
    period = build_analytics_period(
        mode="7_days",
        timezone=ZoneInfo("Europe/Moscow"),
        now=datetime(2026, 7, 25, 12, tzinfo=UTC),
    )

    assert period.local_start_date.isoformat() == "2026-07-19"
    assert period.local_end_date.isoformat() == "2026-07-26"
