from datetime import date, datetime
from uuid import UUID

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
from booking_bot.services.bookings import AppointmentSummary
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


def dates_keyboard(
    dates: list[date],
    *,
    callback_prefix: str = "date",
    back_callback: str = "booking:start",
    back_text: str | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for item in dates:
        builder.button(
            text=f"{WEEKDAYS_RU[item.weekday()]}, {item:%d.%m}",
            callback_data=f"{callback_prefix}:{item.isoformat()}",
        )
    builder.button(
        text=back_text or get_specialist_template().button("back_to_services", "Назад к услугам"),
        callback_data=back_callback,
    )
    builder.button(
        text=get_specialist_template().button("home", "В главное меню"),
        callback_data="menu:home",
    )
    builder.adjust(2)
    return builder.as_markup()


def slots_keyboard(
    slots: list[BookableSlot],
    timezone,
    *,
    callback_prefix: str = "slot",
    back_callback: str = "booking:back:dates",
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for slot in slots:
        local_start = slot.service_start.astimezone(timezone)
        builder.button(
            text=local_start.strftime("%H:%M"),
            callback_data=f"{callback_prefix}:{int(slot.service_start.timestamp())}",
        )
    template = get_specialist_template()
    builder.button(
        text=template.button("back_to_dates", "Назад к датам"),
        callback_data=back_callback,
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


def client_appointments_keyboard(
    appointments: list[AppointmentSummary],
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for item in appointments:
        builder.button(
            text=f"{item.local_start:%d.%m %H:%M} · {item.service_name}",
            callback_data=f"appt:v:{item.appointment_id}",
        )
    builder.button(
        text=get_specialist_template().button("home", "В главное меню"),
        callback_data="menu:home",
    )
    builder.adjust(1)
    return builder.as_markup()


def client_appointment_actions_keyboard(
    appointment: AppointmentSummary,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if appointment.can_change:
        rows.extend(
            [
                [
                    InlineKeyboardButton(
                        text="Перенести запись",
                        callback_data=f"appt:r:{appointment.appointment_id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Отменить запись",
                        callback_data=f"appt:c:{appointment.appointment_id}",
                    )
                ],
            ]
        )
    rows.extend(
        [
            [InlineKeyboardButton(text="К моим записям", callback_data="booking:mine")],
            [
                InlineKeyboardButton(
                    text=get_specialist_template().button("home", "В главное меню"),
                    callback_data="menu:home",
                )
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def client_cancel_confirmation_keyboard(appointment_id: UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, отменить",
                    callback_data=f"appt:cy:{appointment_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Нет, оставить запись",
                    callback_data=f"appt:v:{appointment_id}",
                )
            ],
        ]
    )


def reschedule_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Подтвердить перенос",
                    callback_data="appt:rc",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Оставить прежнее время",
                    callback_data="appt:ra",
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
                    text="Услуги и цены",
                    callback_data="master:services",
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


def master_services_keyboard(services: list[Service]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for service in services:
        status = "✅" if service.is_active else "⏸"
        builder.button(
            text=f"{status} {service.name}",
            callback_data=f"svc:v:{service.id}",
        )
    builder.button(text="➕ Добавить услугу", callback_data="svc:new")
    builder.button(
        text=get_specialist_template().button("specialist_cabinet", "Мой кабинет"),
        callback_data="master:menu",
    )
    builder.adjust(1)
    return builder.as_markup()


def master_service_actions_keyboard(service: Service) -> InlineKeyboardMarkup:
    approval = "✅ Ручное подтверждение" if service.requires_approval else "Автоподтверждение"
    visibility = "Скрыть услугу" if service.is_active else "Опубликовать услугу"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Название",
                    callback_data=f"svc:e:n:{service.id}",
                ),
                InlineKeyboardButton(
                    text="Описание",
                    callback_data=f"svc:e:d:{service.id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Длительность",
                    callback_data=f"svc:e:t:{service.id}",
                ),
                InlineKeyboardButton(
                    text="Цена",
                    callback_data=f"svc:e:p:{service.id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Перерывы",
                    callback_data=f"svc:e:b:{service.id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=approval,
                    callback_data=f"svc:t:r:{service.id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=visibility,
                    callback_data=f"svc:t:a:{service.id}",
                )
            ],
            [InlineKeyboardButton(text="К списку услуг", callback_data="master:services")],
        ]
    )


def master_service_cancel_keyboard(service_id: UUID | None = None) -> InlineKeyboardMarkup:
    callback_data = f"svc:v:{service_id}" if service_id is not None else "master:services"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data=callback_data)],
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
