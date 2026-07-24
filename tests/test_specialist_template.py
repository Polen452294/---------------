from datetime import date
from pathlib import Path

from booking_bot.bot.keyboards import dates_keyboard, main_menu_keyboard
from booking_bot.specialist_config import load_specialist_template

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_specialist_template_contains_single_owner_catalog() -> None:
    template = load_specialist_template(PROJECT_ROOT / "specialist.toml")

    assert template.profile.specialist_name == "Анна"
    assert template.profile.brand_name == "Anna Tattoo"
    assert len(template.services) == 2
    assert len({service.key for service in template.services}) == len(template.services)


def test_client_keyboards_do_not_offer_master_selection() -> None:
    menu = main_menu_keyboard()
    dates = dates_keyboard([date(2026, 7, 25)])
    callbacks = [
        button.callback_data
        for row in [*menu.inline_keyboard, *dates.inline_keyboard]
        for button in row
    ]

    assert "booking:start" in callbacks
    assert all(not callback.startswith("master:") for callback in callbacks if callback)
    assert "booking:back:masters" not in callbacks
