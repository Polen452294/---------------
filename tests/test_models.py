from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from booking_bot.db.models import CalendarEntry


def test_calendar_entries_include_database_overlap_guard() -> None:
    ddl = str(CreateTable(CalendarEntry.__table__).compile(dialect=postgresql.dialect()))

    assert "EXCLUDE USING gist" in ddl
    assert "tstzrange(starts_at, ends_at" in ddl
    assert "state = 'active'" in ddl
