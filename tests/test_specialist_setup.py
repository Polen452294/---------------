from datetime import time

import pytest

from booking_bot.bot.handlers.specialist_setup import _parse_time_range
from booking_bot.bot.keyboards import specialist_setup_days_keyboard


def test_working_hours_parser_accepts_a_positive_interval() -> None:
    assert _parse_time_range("10:00-19:30") == (time(10), time(19, 30))


@pytest.mark.parametrize("value", ["10:00", "19:00-10:00", "10:00-10:00", "ночью"])
def test_working_hours_parser_rejects_invalid_intervals(value: str) -> None:
    with pytest.raises(ValueError):
        _parse_time_range(value)


def test_schedule_day_keyboard_marks_selected_days() -> None:
    keyboard = specialist_setup_days_keyboard({0, 4})
    labels = [button.text for row in keyboard.inline_keyboard for button in row]

    assert "✅ Пн" in labels
    assert "✅ Пт" in labels
    assert "Вт" in labels
