from datetime import UTC, date, datetime
from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile

from booking_bot.bot.handlers.master import _format_month_appointments_table
from booking_bot.bot.keyboards import master_month_schedule_keyboard
from booking_bot.services.appointment_export import build_appointments_xlsx
from booking_bot.services.master_schedule import MasterAppointment


def _appointment(index: int = 0) -> MasterAppointment:
    start = datetime(2026, 8, 3 + index, 10, 30, tzinfo=UTC)
    return MasterAppointment(
        appointment_id=uuid4(),
        service_name="Консультация",
        client_name="Иван Петров",
        client_phone="+79990000000",
        client_comment="Первый визит",
        internal_note="Подготовить эскизы",
        location_name="Студия",
        local_start=start,
        local_end=start.replace(hour=11, minute=30),
        duration_minutes=60,
        status="confirmed",
    )


def test_month_schedule_is_formatted_as_paginated_table() -> None:
    appointments = [_appointment(index) for index in range(20)]

    text, page, page_count = _format_month_appointments_table(
        appointments,
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 30),
        page=1,
    )

    assert page == 1
    assert page_count == 2
    assert "Записи на ближайшие 30 дней" in text
    assert "<pre>" in text
    assert "20" in text


def test_month_schedule_keyboard_contains_navigation_and_excel_export() -> None:
    keyboard = master_month_schedule_keyboard(page=1, page_count=3)
    buttons = [button for row in keyboard.inline_keyboard for button in row]

    assert {button.callback_data for button in buttons} >= {
        "master:month:page:0",
        "master:month:page:2",
        "master:month:export",
    }


def test_excel_export_contains_typed_table_and_appointment_data() -> None:
    content = build_appointments_xlsx(
        [_appointment()],
        specialist_name="Анна",
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 30),
    )

    with ZipFile(BytesIO(content)) as archive:
        names = set(archive.namelist())
        sheet_xml = archive.read("xl/worksheets/sheet1.xml").decode()
        table_xml = archive.read("xl/tables/table1.xml").decode()

    assert {
        "[Content_Types].xml",
        "xl/workbook.xml",
        "xl/worksheets/sheet1.xml",
        "xl/tables/table1.xml",
        "xl/styles.xml",
    } <= names
    assert "Иван Петров" in sheet_xml
    assert "Консультация" in sheet_xml
    assert 'displayName="AppointmentsTable"' in table_xml
    assert 'ref="A5:L6"' in table_xml
