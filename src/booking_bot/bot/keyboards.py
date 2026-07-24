from datetime import date, datetime

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from booking_bot.db.models import Service
from booking_bot.domain.enums import AppointmentStatus
from booking_bot.services.availability import BookableSlot
from booking_bot.services.master_schedule import (
    MasterAppointment,
    MasterTimeBlock,
    WeeklyWorkingInterval,
)
from booking_bot.specialist_config import get_specialist_template

WEEKDAYS_RU = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


def main_menu_keyboard(*, master_access: bool = False) -> InlineKeyboardMarkup:
    template = get_specialist_template()
    rows = [
        [
            InlineKeyboardButton(
                text=template.button("book", "Записаться"),
                callback_data="booking:start",
            )
        ],
        [
            InlineKeyboardButton(
                text=template.button("my_bookings", "Мои записи"),
                callback_data="booking:mine",
            )
        ],
    ]
    if master_access:
        rows.append(
            [
                InlineKeyboardButton(
                    text=template.button("specialist_cabinet", "Мой кабинет"),
                    callback_data="master:menu",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text=template.button("help", "Помощь"),
                callback_data="help",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def services_keyboard(services: list[Service]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for service in services:
        price = (
            f" - {service.price_minor / 100:.0f} {service.currency}"
            if service.price_minor is not None
            else ""
        )
        builder.button(text=f"{service.name}{price}", callback_data=f"service:{service.id}")
    builder.button(
        text=get_specialist_template().button("home", "В главное меню"),
        callback_data="menu:home",
    )
    builder.adjust(1)
    return builder.as_markup()


def dates_keyboard(dates: list[date]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for item in dates:
        builder.button(
            text=f"{WEEKDAYS_RU[item.weekday()]}, {item:%d.%m}",
            callback_data=f"date:{item.isoformat()}",
        )
    builder.button(
        text=get_specialist_template().button("back_to_services", "Назад к услугам"),
        callback_data="booking:start",
    )
    builder.button(
        text=get_specialist_template().button("home", "В главное меню"),
        callback_data="menu:home",
    )
    builder.adjust(2)
    return builder.as_markup()


def slots_keyboard(slots: list[BookableSlot], timezone) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for slot in slots:
        local_start = slot.service_start.astimezone(timezone)
        builder.button(
            text=local_start.strftime("%H:%M"),
            callback_data=f"slot:{int(slot.service_start.timestamp())}",
        )
    template = get_specialist_template()
    builder.button(
        text=template.button("back_to_dates", "Назад к датам"),
        callback_data="booking:back:dates",
    )
    builder.button(
        text=template.button("home", "В главное меню"),
        callback_data="menu:home",
    )
    builder.adjust(3)
    return builder.as_markup()


def confirmation_keyboard() -> InlineKeyboardMarkup:
    template = get_specialist_template()
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=template.button("confirm_booking", "Подтвердить запись"),
                    callback_data="booking:confirm",
                )
            ],
            [
                InlineKeyboardButton(
                    text=template.button("cancel", "Отменить"),
                    callback_data="booking:abort",
                )
            ],
        ]
    )


def phone_keyboard() -> ReplyKeyboardMarkup:
    template = get_specialist_template()
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text=template.button("share_phone", "Отправить мой телефон"),
                    request_contact=True,
                )
            ],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder=template.button(
            "phone_placeholder",
            "Нажмите кнопку или введите номер",
        ),
    )


def master_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Сегодня",
                    callback_data="master:schedule:today",
                ),
                InlineKeyboardButton(
                    text="Завтра",
                    callback_data="master:schedule:tomorrow",
                ),
            ],
            [InlineKeyboardButton(text="Неделя", callback_data="master:schedule:week")],
            [
                InlineKeyboardButton(
                    text="Управление графиком",
                    callback_data="master:availability",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Уведомления",
                    callback_data="master:notifications",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Клиентское меню",
                    callback_data="master:client-menu",
                )
            ],
        ]
    )


def master_appointments_keyboard(
    appointments: list[MasterAppointment],
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for item in appointments:
        builder.button(
            text=f"{item.local_start:%d.%m %H:%M} · {item.service_name}",
            callback_data=f"master:appointment:{item.appointment_id}",
        )
    builder.button(
        text=get_specialist_template().button("specialist_cabinet", "Мой кабинет"),
        callback_data="master:menu",
    )
    builder.adjust(1)
    return builder.as_markup()


def master_appointment_actions_keyboard(
    appointment: MasterAppointment,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if appointment.status == AppointmentStatus.PENDING_APPROVAL.value:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Подтвердить",
                    callback_data=f"master:status:{appointment.appointment_id}:approve",
                )
            ]
        )
    if (
        appointment.status == AppointmentStatus.CONFIRMED.value
        and appointment.local_start <= datetime.now(appointment.local_start.tzinfo)
    ):
        rows.append(
            [
                InlineKeyboardButton(
                    text="Выполнено",
                    callback_data=f"master:status:{appointment.appointment_id}:complete",
                ),
                InlineKeyboardButton(
                    text="Не пришёл",
                    callback_data=f"master:status:{appointment.appointment_id}:noshow",
                ),
            ]
        )
    if appointment.status in {
        AppointmentStatus.PENDING_APPROVAL.value,
        AppointmentStatus.CONFIRMED.value,
    }:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Отменить запись",
                    callback_data=f"master:status:{appointment.appointment_id}:cancel",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="К расписанию", callback_data="master:schedule:today")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def master_availability_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Рабочие часы",
                    callback_data="master:working-hours",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Выходные",
                    callback_data="master:days-off",
                ),
                InlineKeyboardButton(
                    text="Доп. рабочий день",
                    callback_data="master:extra-day",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Заблокировать время",
                    callback_data="master:block:add",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Активные блокировки",
                    callback_data="master:blocks",
                )
            ],
            [InlineKeyboardButton(text="Назад", callback_data="master:menu")],
        ]
    )


def master_weekdays_keyboard(
    rules: list[WeeklyWorkingInterval],
) -> InlineKeyboardMarkup:
    intervals: dict[int, list[str]] = {}
    for rule in rules:
        intervals.setdefault(rule.weekday, []).append(
            f"{rule.start_time:%H:%M}-{rule.end_time:%H:%M}"
        )
    builder = InlineKeyboardBuilder()
    for weekday, name in enumerate(WEEKDAYS_RU):
        hours = ", ".join(intervals.get(weekday, [])) or "выходной"
        builder.button(
            text=f"{name}: {hours}",
            callback_data=f"master:weekday:{weekday}",
        )
    builder.button(text="Назад", callback_data="master:availability")
    builder.adjust(1)
    return builder.as_markup()


def master_days_off_keyboard(
    dates: list[date],
    selected: set[date],
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for item in dates:
        marker = "✅ " if item in selected else ""
        builder.button(
            text=f"{marker}{WEEKDAYS_RU[item.weekday()]}, {item:%d.%m}",
            callback_data=f"master:dayoff:{item.isoformat()}",
        )
    builder.button(text="Назад", callback_data="master:availability")
    builder.adjust(2)
    return builder.as_markup()


def master_blocks_keyboard(blocks: list[MasterTimeBlock]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for block in blocks:
        builder.button(
            text=f"Удалить {block.local_start:%d.%m %H:%M}-{block.local_end:%H:%M}",
            callback_data=f"master:block:remove:{block.block_id}",
        )
    builder.button(text="Назад", callback_data="master:availability")
    builder.adjust(1)
    return builder.as_markup()


def master_notifications_keyboard(enabled: bool) -> InlineKeyboardMarkup:
    state = "включены" if enabled else "выключены"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Новые записи: {state}",
                    callback_data="master:notifications:toggle",
                )
            ],
            [InlineKeyboardButton(text="Назад", callback_data="master:menu")],
        ]
    )
