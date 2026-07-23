import re
from datetime import UTC, date, datetime, time, timedelta
from html import escape
from uuid import UUID
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from booking_bot.bot.keyboards import (
    main_menu_keyboard,
    master_appointment_actions_keyboard,
    master_appointments_keyboard,
    master_availability_keyboard,
    master_blocks_keyboard,
    master_days_off_keyboard,
    master_menu_keyboard,
    master_notifications_keyboard,
    master_weekdays_keyboard,
)
from booking_bot.bot.states import MasterStates
from booking_bot.db.models import Business, Master, TelegramUser
from booking_bot.domain.enums import AppointmentStatus
from booking_bot.services.master_access import get_master_for_user
from booking_bot.services.master_schedule import (
    AppointmentNotFoundError,
    InvalidAppointmentTransitionError,
    MasterAppointment,
    MasterScheduleService,
    ScheduleConflictError,
)
from booking_bot.services.notification_delivery import (
    master_notifications_enabled,
    toggle_master_notifications,
)
from booking_bot.services.users import get_or_create_telegram_user

router = Router(name="master")
schedule_service = MasterScheduleService()

STATUS_LABELS = {
    AppointmentStatus.PENDING_APPROVAL.value: "ожидает подтверждения",
    AppointmentStatus.CONFIRMED.value: "подтверждена",
    AppointmentStatus.COMPLETED.value: "выполнена",
    AppointmentStatus.NO_SHOW.value: "клиент не пришёл",
    AppointmentStatus.CANCELLED_BY_MASTER.value: "отменена мастером",
    AppointmentStatus.CANCELLED_BY_CLIENT.value: "отменена клиентом",
}
STATUS_ACTIONS = {
    "approve": AppointmentStatus.CONFIRMED.value,
    "complete": AppointmentStatus.COMPLETED.value,
    "noshow": AppointmentStatus.NO_SHOW.value,
    "cancel": AppointmentStatus.CANCELLED_BY_MASTER.value,
}
TIME_RANGE_PATTERN = re.compile(r"^(?P<start>\d{2}:\d{2})\s*-\s*(?P<end>\d{2}:\d{2})$")
DATED_RANGE_PATTERN = re.compile(
    r"^(?P<date>\d{2}\.\d{2}\.\d{4})\s+"
    r"(?P<start>\d{2}:\d{2})\s*-\s*(?P<end>\d{2}:\d{2})"
    r"(?:\s+(?P<reason>.+))?$"
)


async def _master_for_actor(
    actor,
    session: AsyncSession,
    business_id: UUID,
) -> tuple[TelegramUser, Master] | None:
    if actor is None:
        return None
    user = await get_or_create_telegram_user(session, actor)
    master = await get_master_for_user(
        session,
        business_id=business_id,
        user_id=user.id,
    )
    return (user, master) if master is not None else None


async def _master_timezone(
    session: AsyncSession,
    business_id: UUID,
    master: Master,
) -> ZoneInfo:
    business = await session.get(Business, business_id)
    if business is None:
        raise RuntimeError("Business not found")
    return ZoneInfo(master.timezone or business.timezone)


async def _edit(callback: CallbackQuery, text: str, **kwargs) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text(text, **kwargs)


def _format_appointment(item: MasterAppointment) -> str:
    client_name = escape(item.client_name or "Клиент")
    phone = escape(item.client_phone or "не указан")
    location = f"\nМесто: <b>{escape(item.location_name)}</b>" if item.location_name else ""
    return (
        f"<b>{item.local_start:%d.%m.%Y %H:%M}-{item.local_end:%H:%M}</b>\n"
        f"Услуга: <b>{escape(item.service_name)}</b>\n"
        f"Клиент: <b>{client_name}</b>\n"
        f"Телефон: <code>{phone}</code>\n"
        f"Статус: <b>{STATUS_LABELS.get(item.status, escape(item.status))}</b>"
        f"{location}"
    )


async def _deny(callback: CallbackQuery) -> None:
    await callback.answer("Кабинет доступен только привязанному мастеру", show_alert=True)


@router.callback_query(F.data == "master:menu")
async def master_menu(
    callback: CallbackQuery,
    state: FSMContext,
    db_session: AsyncSession,
    business_id: UUID,
) -> None:
    context = await _master_for_actor(callback.from_user, db_session, business_id)
    if context is None:
        await _deny(callback)
        return
    _user, master = context
    await state.clear()
    await _edit(
        callback,
        f"Кабинет мастера <b>{escape(master.display_name)}</b>:",
        reply_markup=master_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "master:client-menu")
async def master_client_menu(
    callback: CallbackQuery,
    state: FSMContext,
    db_session: AsyncSession,
    business_id: UUID,
) -> None:
    context = await _master_for_actor(callback.from_user, db_session, business_id)
    if context is None:
        await _deny(callback)
        return
    await state.clear()
    await _edit(
        callback,
        "Клиентское меню:",
        reply_markup=main_menu_keyboard(master_access=True),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("master:schedule:"))
async def master_schedule(
    callback: CallbackQuery,
    db_session: AsyncSession,
    business_id: UUID,
) -> None:
    context = await _master_for_actor(callback.from_user, db_session, business_id)
    if context is None:
        await _deny(callback)
        return
    _user, master = context
    mode = (callback.data or "").rsplit(":", 1)[-1]
    timezone = await _master_timezone(db_session, business_id, master)
    today = datetime.now(UTC).astimezone(timezone).date()
    if mode == "today":
        start_date, days, title = today, 1, "Расписание на сегодня"
    elif mode == "tomorrow":
        start_date, days, title = today + timedelta(days=1), 1, "Расписание на завтра"
    elif mode == "week":
        start_date, days, title = today, 7, "Расписание на 7 дней"
    else:
        await callback.answer("Неизвестный период", show_alert=True)
        return

    appointments = await schedule_service.list_appointments(
        db_session,
        business_id=business_id,
        master=master,
        start_date=start_date,
        days=days,
    )
    text = (
        f"<b>{title}</b>\n\nВыберите запись для просмотра."
        if appointments
        else f"<b>{title}</b>\n\nЗаписей нет."
    )
    await _edit(
        callback,
        text,
        reply_markup=master_appointments_keyboard(appointments),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("master:appointment:"))
async def master_appointment(
    callback: CallbackQuery,
    db_session: AsyncSession,
    business_id: UUID,
) -> None:
    context = await _master_for_actor(callback.from_user, db_session, business_id)
    if context is None:
        await _deny(callback)
        return
    _user, master = context
    try:
        appointment_id = UUID((callback.data or "").rsplit(":", 1)[-1])
        appointment = await schedule_service.get_appointment(
            db_session,
            business_id=business_id,
            master=master,
            appointment_id=appointment_id,
        )
    except (ValueError, AppointmentNotFoundError):
        await callback.answer("Запись не найдена", show_alert=True)
        return
    await _edit(
        callback,
        _format_appointment(appointment),
        reply_markup=master_appointment_actions_keyboard(appointment),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("master:status:"))
async def master_status(
    callback: CallbackQuery,
    db_session: AsyncSession,
    business_id: UUID,
) -> None:
    context = await _master_for_actor(callback.from_user, db_session, business_id)
    if context is None:
        await _deny(callback)
        return
    user, master = context
    try:
        _prefix, _status, appointment_raw, action = (callback.data or "").split(":")
        appointment_id = UUID(appointment_raw)
        new_status = STATUS_ACTIONS[action]
        appointment = await schedule_service.change_appointment_status(
            db_session,
            business_id=business_id,
            master=master,
            appointment_id=appointment_id,
            actor_user_id=user.id,
            new_status=new_status,
        )
    except (ValueError, KeyError, AppointmentNotFoundError):
        await callback.answer("Запись не найдена", show_alert=True)
        return
    except InvalidAppointmentTransitionError:
        await callback.answer("Статус записи уже изменился", show_alert=True)
        return
    await _edit(
        callback,
        _format_appointment(appointment),
        reply_markup=master_appointment_actions_keyboard(appointment),
    )
    await callback.answer("Статус обновлён")


@router.callback_query(F.data == "master:availability")
async def master_availability(
    callback: CallbackQuery,
    state: FSMContext,
    db_session: AsyncSession,
    business_id: UUID,
) -> None:
    if await _master_for_actor(callback.from_user, db_session, business_id) is None:
        await _deny(callback)
        return
    await state.clear()
    await _edit(
        callback,
        "<b>Управление графиком</b>\n\n"
        "Рабочие часы создают свободные окна. Выходные и блокировки исключают время.",
        reply_markup=master_availability_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "master:working-hours")
async def master_working_hours(
    callback: CallbackQuery,
    db_session: AsyncSession,
    business_id: UUID,
) -> None:
    context = await _master_for_actor(callback.from_user, db_session, business_id)
    if context is None:
        await _deny(callback)
        return
    _user, master = context
    rules = await schedule_service.list_weekly_rules(
        db_session,
        business_id=business_id,
        master_id=master.id,
    )
    await _edit(
        callback,
        "<b>Рабочие часы</b>\n\nВыберите день недели для изменения:",
        reply_markup=master_weekdays_keyboard(rules),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("master:weekday:"))
async def master_select_weekday(
    callback: CallbackQuery,
    state: FSMContext,
    db_session: AsyncSession,
    business_id: UUID,
) -> None:
    if await _master_for_actor(callback.from_user, db_session, business_id) is None:
        await _deny(callback)
        return
    try:
        weekday = int((callback.data or "").rsplit(":", 1)[-1])
        if weekday not in range(7):
            raise ValueError
    except ValueError:
        await callback.answer("Некорректный день", show_alert=True)
        return
    await state.set_state(MasterStates.waiting_weekday_hours)
    await state.update_data(master_weekday=weekday)
    await _edit(
        callback,
        "Отправьте рабочие часы в формате <code>10:00-19:00</code>.\n"
        "Чтобы сделать день нерабочим, отправьте слово <code>выходной</code>.",
    )
    await callback.answer()


@router.message(MasterStates.waiting_weekday_hours, F.text)
async def master_receive_weekday_hours(
    message: Message,
    state: FSMContext,
    db_session: AsyncSession,
    business_id: UUID,
) -> None:
    context = await _master_for_actor(message.from_user, db_session, business_id)
    if context is None:
        await state.clear()
        await message.answer("Кабинет мастера недоступен.")
        return
    _user, master = context
    data = await state.get_data()
    weekday = int(data["master_weekday"])
    value = (message.text or "").strip().lower()
    if value == "выходной":
        start_time = end_time = None
    else:
        match = TIME_RANGE_PATTERN.fullmatch(value)
        if match is None:
            await message.answer("Используйте формат <code>10:00-19:00</code>.")
            return
        try:
            start_time = time.fromisoformat(match.group("start"))
            end_time = time.fromisoformat(match.group("end"))
            if end_time <= start_time:
                raise ValueError
        except ValueError:
            await message.answer("Проверьте время начала и окончания.")
            return
    await schedule_service.set_weekday_hours(
        db_session,
        business_id=business_id,
        master_id=master.id,
        weekday=weekday,
        start_time=start_time,
        end_time=end_time,
    )
    await state.clear()
    rules = await schedule_service.list_weekly_rules(
        db_session,
        business_id=business_id,
        master_id=master.id,
    )
    await message.answer(
        "Рабочие часы обновлены.",
        reply_markup=master_weekdays_keyboard(rules),
    )


async def _show_days_off(
    callback: CallbackQuery,
    session: AsyncSession,
    business_id: UUID,
    master: Master,
) -> None:
    timezone = await _master_timezone(session, business_id, master)
    today = datetime.now(UTC).astimezone(timezone).date()
    dates = [today + timedelta(days=offset) for offset in range(21)]
    selected = await schedule_service.list_day_off_dates(
        session,
        business_id=business_id,
        master_id=master.id,
        start_date=today,
        days=len(dates),
    )
    await _edit(
        callback,
        "<b>Выходные</b>\n\nНажмите дату, чтобы включить или убрать выходной:",
        reply_markup=master_days_off_keyboard(dates, selected),
    )


@router.callback_query(F.data == "master:days-off")
async def master_days_off(
    callback: CallbackQuery,
    db_session: AsyncSession,
    business_id: UUID,
) -> None:
    context = await _master_for_actor(callback.from_user, db_session, business_id)
    if context is None:
        await _deny(callback)
        return
    _user, master = context
    await _show_days_off(callback, db_session, business_id, master)
    await callback.answer()


@router.callback_query(F.data.startswith("master:dayoff:"))
async def master_toggle_day_off(
    callback: CallbackQuery,
    db_session: AsyncSession,
    business_id: UUID,
) -> None:
    context = await _master_for_actor(callback.from_user, db_session, business_id)
    if context is None:
        await _deny(callback)
        return
    _user, master = context
    try:
        local_date = date.fromisoformat((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("Некорректная дата", show_alert=True)
        return
    enabled = await schedule_service.toggle_day_off(
        db_session,
        business_id=business_id,
        master_id=master.id,
        local_date=local_date,
    )
    await _show_days_off(callback, db_session, business_id, master)
    await callback.answer("Выходной добавлен" if enabled else "Выходной удалён")


@router.callback_query(F.data == "master:block:add")
async def master_add_block(
    callback: CallbackQuery,
    state: FSMContext,
    db_session: AsyncSession,
    business_id: UUID,
) -> None:
    if await _master_for_actor(callback.from_user, db_session, business_id) is None:
        await _deny(callback)
        return
    await state.set_state(MasterStates.waiting_time_block)
    await _edit(
        callback,
        "Отправьте интервал и необязательную причину:\n"
        "<code>25.07.2026 14:00-16:00 личные дела</code>",
    )
    await callback.answer()


@router.message(MasterStates.waiting_time_block, F.text)
async def master_receive_block(
    message: Message,
    state: FSMContext,
    db_session: AsyncSession,
    business_id: UUID,
) -> None:
    context = await _master_for_actor(message.from_user, db_session, business_id)
    if context is None:
        await state.clear()
        await message.answer("Кабинет мастера недоступен.")
        return
    _user, master = context
    match = DATED_RANGE_PATTERN.fullmatch((message.text or "").strip())
    if match is None:
        await message.answer("Используйте формат <code>25.07.2026 14:00-16:00 причина</code>.")
        return
    try:
        local_date = datetime.strptime(match.group("date"), "%d.%m.%Y").date()
        start_time = time.fromisoformat(match.group("start"))
        end_time = time.fromisoformat(match.group("end"))
        timezone = await _master_timezone(db_session, business_id, master)
        starts_at = datetime.combine(local_date, start_time, timezone)
        ends_at = datetime.combine(local_date, end_time, timezone)
        await schedule_service.create_time_block(
            db_session,
            business_id=business_id,
            master_id=master.id,
            starts_at=starts_at,
            ends_at=ends_at,
            reason=match.group("reason"),
        )
    except ValueError:
        await message.answer("Проверьте дату и время интервала.")
        return
    except ScheduleConflictError:
        await message.answer("Это время пересекается с записью или другой блокировкой.")
        return
    await state.clear()
    await message.answer(
        "Время заблокировано.",
        reply_markup=master_availability_keyboard(),
    )


@router.callback_query(F.data == "master:blocks")
async def master_blocks(
    callback: CallbackQuery,
    db_session: AsyncSession,
    business_id: UUID,
) -> None:
    context = await _master_for_actor(callback.from_user, db_session, business_id)
    if context is None:
        await _deny(callback)
        return
    _user, master = context
    blocks = await schedule_service.list_time_blocks(
        db_session,
        business_id=business_id,
        master=master,
    )
    text = (
        "<b>Активные блокировки</b>\n\nНажмите блокировку, чтобы удалить."
        if blocks
        else "<b>Активные блокировки</b>\n\nБлокировок нет."
    )
    await _edit(callback, text, reply_markup=master_blocks_keyboard(blocks))
    await callback.answer()


@router.callback_query(F.data.startswith("master:block:remove:"))
async def master_remove_block(
    callback: CallbackQuery,
    db_session: AsyncSession,
    business_id: UUID,
) -> None:
    context = await _master_for_actor(callback.from_user, db_session, business_id)
    if context is None:
        await _deny(callback)
        return
    _user, master = context
    try:
        block_id = UUID((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("Некорректная блокировка", show_alert=True)
        return
    await schedule_service.release_time_block(
        db_session,
        business_id=business_id,
        master_id=master.id,
        block_id=block_id,
    )
    blocks = await schedule_service.list_time_blocks(
        db_session,
        business_id=business_id,
        master=master,
    )
    await _edit(
        callback,
        "<b>Активные блокировки</b>",
        reply_markup=master_blocks_keyboard(blocks),
    )
    await callback.answer("Блокировка удалена")


@router.callback_query(F.data == "master:extra-day")
async def master_extra_day(
    callback: CallbackQuery,
    state: FSMContext,
    db_session: AsyncSession,
    business_id: UUID,
) -> None:
    if await _master_for_actor(callback.from_user, db_session, business_id) is None:
        await _deny(callback)
        return
    await state.set_state(MasterStates.waiting_extra_day)
    await _edit(
        callback,
        "Отправьте дату и рабочие часы дополнительного дня:\n<code>26.07.2026 11:00-17:00</code>",
    )
    await callback.answer()


@router.message(MasterStates.waiting_extra_day, F.text)
async def master_receive_extra_day(
    message: Message,
    state: FSMContext,
    db_session: AsyncSession,
    business_id: UUID,
) -> None:
    context = await _master_for_actor(message.from_user, db_session, business_id)
    if context is None:
        await state.clear()
        await message.answer("Кабинет мастера недоступен.")
        return
    _user, master = context
    match = DATED_RANGE_PATTERN.fullmatch((message.text or "").strip())
    if match is None:
        await message.answer("Используйте формат <code>26.07.2026 11:00-17:00</code>.")
        return
    try:
        local_date = datetime.strptime(match.group("date"), "%d.%m.%Y").date()
        start_time = time.fromisoformat(match.group("start"))
        end_time = time.fromisoformat(match.group("end"))
        await schedule_service.add_extra_day(
            db_session,
            business_id=business_id,
            master_id=master.id,
            local_date=local_date,
            start_time=start_time,
            end_time=end_time,
        )
    except ValueError:
        await message.answer("Проверьте дату и рабочие часы.")
        return
    await state.clear()
    await message.answer(
        "Дополнительный рабочий день добавлен.",
        reply_markup=master_availability_keyboard(),
    )


@router.callback_query(F.data == "master:notifications")
async def master_notifications(
    callback: CallbackQuery,
    db_session: AsyncSession,
    business_id: UUID,
) -> None:
    context = await _master_for_actor(callback.from_user, db_session, business_id)
    if context is None:
        await _deny(callback)
        return
    user, _master = context
    enabled = await master_notifications_enabled(
        db_session,
        business_id=business_id,
        user_id=user.id,
    )
    await _edit(
        callback,
        "<b>Уведомления мастера</b>\n\nЗдесь можно отключить сообщения о новых записях.",
        reply_markup=master_notifications_keyboard(enabled),
    )
    await callback.answer()


@router.callback_query(F.data == "master:notifications:toggle")
async def master_toggle_notifications(
    callback: CallbackQuery,
    db_session: AsyncSession,
    business_id: UUID,
) -> None:
    context = await _master_for_actor(callback.from_user, db_session, business_id)
    if context is None:
        await _deny(callback)
        return
    user, _master = context
    enabled = await toggle_master_notifications(
        db_session,
        business_id=business_id,
        user_id=user.id,
    )
    await _edit(
        callback,
        "<b>Уведомления мастера</b>\n\nЗдесь можно отключить сообщения о новых записях.",
        reply_markup=master_notifications_keyboard(enabled),
    )
    await callback.answer("Уведомления включены" if enabled else "Уведомления выключены")
