from datetime import UTC, datetime
from uuid import uuid4

from booking_bot.bot.handlers.booking import _build_appointment_ics
from booking_bot.bot.keyboards import (
    client_appointment_actions_keyboard,
    client_booking_sections_keyboard,
)
from booking_bot.services.bookings import AppointmentSummary
from booking_bot.services.users import normalize_phone


def _summary(*, status: str = "completed") -> AppointmentSummary:
    return AppointmentSummary(
        appointment_id=uuid4(),
        service_id=uuid4(),
        service_name="Консультация",
        master_name="Анна",
        location_name="Студия",
        location_address="Москва, Тверская 1",
        local_start=datetime(2026, 8, 3, 10, tzinfo=UTC),
        local_end=datetime(2026, 8, 3, 11, tzinfo=UTC),
        status=status,
        can_change=False,
        change_deadline=datetime(2026, 8, 2, 10, tzinfo=UTC),
    )


def test_phone_normalization_uses_single_format() -> None:
    assert normalize_phone("+7 (999) 111-22-33") == "+79991112233"
    assert normalize_phone("8 999 111 22 33") == "+79991112233"
    assert normalize_phone("123") is None


def test_client_history_and_appointment_actions_are_available() -> None:
    sections = client_booking_sections_keyboard()
    section_callbacks = {
        button.callback_data for row in sections.inline_keyboard for button in row
    }
    assert {
        "booking:list:upcoming",
        "booking:list:past",
        "booking:list:cancelled",
    }.issubset(section_callbacks)

    actions = client_appointment_actions_keyboard(_summary())
    action_callbacks = {
        button.callback_data for row in actions.inline_keyboard for button in row
    }
    assert any(callback and callback.startswith("appt:repeat:") for callback in action_callbacks)
    assert any(callback and callback.startswith("appt:ics:") for callback in action_callbacks)
    assert any(callback and callback.startswith("appt:contact:") for callback in action_callbacks)


def test_calendar_export_contains_utc_event_and_location() -> None:
    payload = _build_appointment_ics(_summary()).decode()

    assert "BEGIN:VEVENT" in payload
    assert "DTSTART:20260803T100000Z" in payload
    assert "SUMMARY:Консультация" in payload
    assert "LOCATION:Студия\\, Москва\\, Тверская 1" in payload
