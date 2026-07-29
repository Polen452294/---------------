import re
from datetime import time
from html import escape
from uuid import UUID

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, User
from sqlalchemy.ext.asyncio import AsyncSession

from booking_bot.bot.keyboards import (
    master_menu_keyboard,
    specialist_setup_bio_keyboard,
    specialist_setup_cancel_keyboard,
    specialist_setup_confirmation_keyboard,
    specialist_setup_days_keyboard,
)
from booking_bot.bot.states import SpecialistSetupStates
from booking_bot.db.models import Master
from booking_bot.services.master_access import get_master_for_user
from booking_bot.services.master_schedule import MasterScheduleService
from booking_bot.services.users import get_or_create_telegram_user

router = Router(name="specialist_setup")
schedule_service = MasterScheduleService()

WEEKDAY_NAMES = (
    "понедельник",
    "вторник",
    "среду",
    "четверг",
    "пятницу",
    "субботу",
    "воскресенье",
)
WEEKDAY_LABELS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
TIME_RANGE_PATTERN = re.compile(r"^(?P<start>\d{2}:\d{2})\s*-\s*(?P<end>\d{2}:\d{2})$")


async def _master_for_actor(
    actor: User | None,
    session: AsyncSession,
    business_id: UUID,
) -> Master | None:
    if actor is None:
        return None
    user = await get_or_create_telegram_user(session, actor)
    return await get_master_for_user(
        session,
        business_id=business_id,
        user_id=user.id,
    )


async def _selected_workdays(
    session: AsyncSession,
    *,
    business_id: UUID,
    master_id: UUID,
) -> list[int]:
    rules = await schedule_service.list_weekly_rules(
        session,
        business_id=business_id,
        master_id=master_id,
    )
    return sorted({rule.weekday for rule in rules})


async def begin_specialist_onboarding(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    *,
    business_id: UUID,
    master: Master,
) -> None:
    selected = await _selected_workdays(
        session,
        business_id=business_id,
        master_id=master.id,
    )
    await state.clear()
    await state.set_state(SpecialistSetupStates.waiting_name)
    await state.update_data(
        setup_mode="onboarding",
        setup_name=master.display_name,
        setup_bio=master.bio or "",
        selected_weekdays=selected,
    )
    await message.answer(
        f"Профиль специалиста <b>{escape(master.display_name)}</b> успешно привязан.\n\n"
        "Теперь настроим профиль и недельное расписание. Как вас зовут?\n"
        "Отправьте имя, которое будут видеть клиенты.",
        reply_markup=specialist_setup_cancel_keyboard(),
    )


def _parse_time_range(value: str) -> tuple[time, time]:
    match = TIME_RANGE_PATTERN.fullmatch(value.strip())
    if match is None:
        raise ValueError("invalid time range")
    start_time = time.fromisoformat(match.group("start"))
    end_time = time.fromisoformat(match.group("end"))
    if end_time <= start_time:
        raise ValueError("end time must be after start time")
    return start_time, end_time


def _schedule_from_data(data: dict) -> dict[int, tuple[time, time]]:
    raw_intervals = data.get("schedule_intervals", {})
    return {
        int(weekday): (
            time.fromisoformat(interval[0]),
            time.fromisoformat(interval[1]),
        )
        for weekday, interval in raw_intervals.items()
    }


def _schedule_summary(data: dict) -> str:
    schedule = _schedule_from_data(data)
    lines = [
        f"• {WEEKDAY_LABELS[weekday]}: {start:%H:%M}–{end:%H:%M}"
        for weekday, (start, end) in sorted(schedule.items())
    ]
    profile = ""
    if data.get("setup_mode") != "schedule":
        name = escape(str(data.get("setup_name", "")).strip())
        bio = escape(str(data.get("setup_bio", "")).strip()) or "не указано"
        profile = f"<b>Имя:</b> {name}\n<b>Описание:</b> {bio}\n\n"
    return (
        "<b>Проверьте настройки</b>\n\n"
        f"{profile}"
        "<b>Недельное расписание:</b>\n"
        + "\n".join(lines)
        + "\n\nПосле сохранения новые свободные окна будут рассчитаны по этому графику."
    )


async def _edit_callback(callback: CallbackQuery, text: str, **kwargs) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text(text, **kwargs)


async def _show_days(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    selected = set(data.get("selected_weekdays", []))
    await state.set_state(SpecialistSetupStates.selecting_days)
    await _edit_callback(
        callback,
        "<b>Рабочие дни</b>\n\n"
        "Отметьте дни, в которые принимаете клиентов. Затем нажмите «Продолжить».",
        reply_markup=specialist_setup_days_keyboard(selected),
    )


@router.callback_query(F.data == "setup:profile:start")
async def start_profile_setup(
    callback: CallbackQuery,
    state: FSMContext,
    db_session: AsyncSession,
    business_id: UUID,
) -> None:
    master = await _master_for_actor(callback.from_user, db_session, business_id)
    if master is None:
        await callback.answer("Кабинет специалиста недоступен", show_alert=True)
        return
    selected = await _selected_workdays(
        db_session,
        business_id=business_id,
        master_id=master.id,
    )
    await state.clear()
    await state.set_state(SpecialistSetupStates.waiting_name)
    await state.update_data(
        setup_mode="profile",
        setup_name=master.display_name,
        setup_bio=master.bio or "",
        selected_weekdays=selected,
    )
    await _edit_callback(
        callback,
        "<b>Профиль и расписание</b>\n\n"
        f"Текущее имя: <b>{escape(master.display_name)}</b>\n"
        "Отправьте новое имя или повторите текущее.",
        reply_markup=specialist_setup_cancel_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "setup:schedule:start")
async def start_schedule_setup(
    callback: CallbackQuery,
    state: FSMContext,
    db_session: AsyncSession,
    business_id: UUID,
) -> None:
    master = await _master_for_actor(callback.from_user, db_session, business_id)
    if master is None:
        await callback.answer("Кабинет специалиста недоступен", show_alert=True)
        return
    selected = await _selected_workdays(
        db_session,
        business_id=business_id,
        master_id=master.id,
    )
    await state.clear()
    await state.update_data(
        setup_mode="schedule",
        selected_weekdays=selected,
    )
    await _show_days(callback, state)
    await callback.answer()


@router.message(SpecialistSetupStates.waiting_name, F.text)
async def receive_specialist_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if name.startswith("/"):
        return
    if not 2 <= len(name) <= 160:
        await message.answer("Имя должно содержать от 2 до 160 символов.")
        return
    await state.update_data(setup_name=name)
    await state.set_state(SpecialistSetupStates.waiting_bio)
    await message.answer(
        "Коротко опишите специализацию — например: "
        "<i>тату-мастер, цветные и графические работы</i>.\n"
        "Описание будет показано клиентам.",
        reply_markup=specialist_setup_bio_keyboard(),
    )


@router.message(SpecialistSetupStates.waiting_bio, F.text)
async def receive_specialist_bio(message: Message, state: FSMContext) -> None:
    bio = (message.text or "").strip()
    if bio.startswith("/"):
        return
    if not 2 <= len(bio) <= 1000:
        await message.answer("Описание должно содержать от 2 до 1000 символов.")
        return
    await state.update_data(setup_bio=bio)
    selected = set((await state.get_data()).get("selected_weekdays", []))
    await state.set_state(SpecialistSetupStates.selecting_days)
    await message.answer(
        "<b>Рабочие дни</b>\n\n"
        "Отметьте дни, в которые принимаете клиентов. Затем нажмите «Продолжить».",
        reply_markup=specialist_setup_days_keyboard(selected),
    )


@router.callback_query(SpecialistSetupStates.waiting_bio, F.data == "setup:bio:skip")
async def skip_specialist_bio(callback: CallbackQuery, state: FSMContext) -> None:
    await _show_days(callback, state)
    await callback.answer()


@router.callback_query(
    SpecialistSetupStates.selecting_days,
    F.data.startswith("setup:day:"),
)
async def toggle_setup_day(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        weekday = int((callback.data or "").rsplit(":", 1)[-1])
        if weekday not in range(7):
            raise ValueError
    except ValueError:
        await callback.answer("Некорректный день", show_alert=True)
        return
    data = await state.get_data()
    selected = set(data.get("selected_weekdays", []))
    if weekday in selected:
        selected.remove(weekday)
    else:
        selected.add(weekday)
    await state.update_data(selected_weekdays=sorted(selected))
    await _edit_callback(
        callback,
        "<b>Рабочие дни</b>\n\n"
        "Отметьте дни, в которые принимаете клиентов. Затем нажмите «Продолжить».",
        reply_markup=specialist_setup_days_keyboard(selected),
    )
    await callback.answer()


@router.callback_query(
    SpecialistSetupStates.selecting_days,
    F.data.startswith("setup:preset:"),
)
async def apply_setup_day_preset(callback: CallbackQuery, state: FSMContext) -> None:
    preset = (callback.data or "").rsplit(":", 1)[-1]
    selected = {
        "weekdays": set(range(5)),
        "all": set(range(7)),
        "clear": set(),
    }.get(preset)
    if selected is None:
        await callback.answer("Некорректный набор дней", show_alert=True)
        return
    await state.update_data(selected_weekdays=sorted(selected))
    await _edit_callback(
        callback,
        "<b>Рабочие дни</b>\n\n"
        "Отметьте дни, в которые принимаете клиентов. Затем нажмите «Продолжить».",
        reply_markup=specialist_setup_days_keyboard(selected),
    )
    await callback.answer()


@router.callback_query(
    SpecialistSetupStates.selecting_days,
    F.data == "setup:days:done",
)
async def finish_setup_days(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    selected = sorted(set(data.get("selected_weekdays", [])))
    if not selected:
        await callback.answer("Выберите хотя бы один рабочий день", show_alert=True)
        return
    await state.update_data(
        selected_weekdays=selected,
        schedule_intervals={},
        schedule_index=0,
    )
    await state.set_state(SpecialistSetupStates.waiting_day_hours)
    await _edit_callback(
        callback,
        f"Во сколько вы работаете в <b>{WEEKDAY_NAMES[selected[0]]}</b>?\n"
        "Отправьте интервал в формате <code>10:00-19:00</code>.",
        reply_markup=specialist_setup_cancel_keyboard(),
    )
    await callback.answer()


@router.message(SpecialistSetupStates.waiting_day_hours, F.text)
async def receive_setup_day_hours(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if value.startswith("/"):
        return
    try:
        start_time, end_time = _parse_time_range(value)
    except ValueError:
        await message.answer(
            "Проверьте интервал. Используйте формат <code>10:00-19:00</code>, "
            "время окончания должно быть позже начала."
        )
        return

    data = await state.get_data()
    selected = list(data.get("selected_weekdays", []))
    index = int(data.get("schedule_index", 0))
    if index >= len(selected):
        await state.clear()
        await message.answer(
            "Настройка устарела. Начните заново.",
            reply_markup=master_menu_keyboard(),
        )
        return
    weekday = selected[index]
    intervals = dict(data.get("schedule_intervals", {}))
    intervals[str(weekday)] = [
        start_time.isoformat(timespec="minutes"),
        end_time.isoformat(timespec="minutes"),
    ]
    index += 1
    await state.update_data(schedule_intervals=intervals, schedule_index=index)
    if index < len(selected):
        await message.answer(
            f"Во сколько вы работаете в <b>{WEEKDAY_NAMES[selected[index]]}</b>?\n"
            "Отправьте интервал в формате <code>10:00-19:00</code>.",
            reply_markup=specialist_setup_cancel_keyboard(),
        )
        return

    await state.set_state(SpecialistSetupStates.confirming)
    await message.answer(
        _schedule_summary(await state.get_data()),
        reply_markup=specialist_setup_confirmation_keyboard(),
    )


@router.callback_query(
    SpecialistSetupStates.confirming,
    F.data == "setup:days:change",
)
async def change_setup_days(callback: CallbackQuery, state: FSMContext) -> None:
    await _show_days(callback, state)
    await callback.answer()


@router.callback_query(
    SpecialistSetupStates.confirming,
    F.data == "setup:save",
)
async def save_specialist_setup(
    callback: CallbackQuery,
    state: FSMContext,
    db_session: AsyncSession,
    business_id: UUID,
) -> None:
    master = await _master_for_actor(callback.from_user, db_session, business_id)
    if master is None:
        await state.clear()
        await callback.answer("Кабинет специалиста недоступен", show_alert=True)
        return
    data = await state.get_data()
    try:
        schedule = _schedule_from_data(data)
        if not schedule:
            raise ValueError
    except (TypeError, ValueError, KeyError):
        await state.clear()
        await callback.answer("Настройка устарела. Начните заново.", show_alert=True)
        return

    if data.get("setup_mode") != "schedule":
        name = str(data.get("setup_name", "")).strip()
        if not 2 <= len(name) <= 160:
            await callback.answer("Проверьте имя специалиста", show_alert=True)
            return
        master.display_name = name
        master.bio = str(data.get("setup_bio", "")).strip() or None

    await schedule_service.replace_weekly_schedule(
        db_session,
        business_id=business_id,
        master_id=master.id,
        schedule=schedule,
    )
    await state.clear()
    await _edit_callback(
        callback,
        "Настройки сохранены.\n\n"
        "Новые свободные окна уже рассчитываются по обновлённому недельному графику. "
        "Существующие записи, назначенные выходные и блокировки сохранены.",
        reply_markup=master_menu_keyboard(),
    )
    await callback.answer("Расписание обновлено")


@router.callback_query(F.data == "setup:cancel")
async def cancel_specialist_setup(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _edit_callback(
        callback,
        "Настройка отменена. К ней можно вернуться через «Профиль и расписание».",
        reply_markup=master_menu_keyboard(),
    )
    await callback.answer("Отменено")
