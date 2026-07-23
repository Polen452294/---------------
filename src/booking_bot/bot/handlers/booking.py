import re
from datetime import UTC, date, datetime, timedelta
from html import escape
from uuid import UUID
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from booking_bot.bot.keyboards import (
    confirmation_keyboard,
    dates_keyboard,
    main_menu_keyboard,
    masters_keyboard,
    phone_keyboard,
    services_keyboard,
    slots_keyboard,
)
from booking_bot.bot.states import BookingStates
from booking_bot.config import Settings, get_settings
from booking_bot.db.models import Business, Master, MasterService, Service, TelegramUser
from booking_bot.services.availability import AvailabilityService, BookingConfigurationError
from booking_bot.services.bookings import (
    BookingService,
    ClientPhoneRequiredError,
    HoldExpiredError,
    HoldNotFoundError,
    HoldSummary,
    SlotUnavailableError,
)
from booking_bot.services.users import get_or_create_telegram_user, set_user_phone

router = Router(name="booking")


def _settings() -> Settings:
    return get_settings()


def _message_from_callback(callback: CallbackQuery) -> Message | None:
    return callback.message if isinstance(callback.message, Message) else None


async def _edit_or_answer(callback: CallbackQuery, text: str, **kwargs) -> None:
    message = _message_from_callback(callback)
    if message is not None:
        await message.edit_text(text, **kwargs)


def _format_hold(summary: HoldSummary) -> str:
    location = f"\nАдрес: <b>{escape(summary.location_name)}</b>" if summary.location_name else ""
    return (
        "Проверьте запись:\n\n"
        f"Услуга: <b>{escape(summary.service_name)}</b>\n"
        f"Мастер: <b>{escape(summary.master_name)}</b>\n"
        f"Дата: <b>{summary.local_start:%d.%m.%Y}</b>\n"
        f"Время: <b>{summary.local_start:%H:%M}-{summary.local_end:%H:%M}</b>"
        f"{location}\n\n"
        "Время временно удерживается за вами."
    )


async def _show_confirmation(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    data = await state.get_data()
    try:
        summary = await BookingService(_settings()).get_hold_summary(
            session,
            hold_id=UUID(data["hold_id"]),
            client_id=UUID(data["client_id"]),
        )
    except (KeyError, ValueError, HoldNotFoundError):
        await state.clear()
        await message.answer(
            "Не удалось найти удерживаемое время. Начните запись заново.",
            reply_markup=main_menu_keyboard(),
        )
        return

    await state.set_state(BookingStates.confirming)
    await message.answer(_format_hold(summary), reply_markup=confirmation_keyboard())


async def _show_dates(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    business_id: UUID,
) -> None:
    data = await state.get_data()
    master_id = UUID(data["master_id"])
    master = await session.get(Master, master_id)
    business = await session.get(Business, business_id)
    if master is None or business is None:
        await callback.answer("Мастер больше недоступен", show_alert=True)
        return
    timezone = ZoneInfo(master.timezone or business.timezone)
    today = datetime.now(UTC).astimezone(timezone).date()
    dates = [today + timedelta(days=offset) for offset in range(_settings().booking_dates_shown)]
    await state.set_state(BookingStates.selecting_date)
    await _edit_or_answer(
        callback,
        "Выберите дату:",
        reply_markup=dates_keyboard(dates),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:home")
async def home_callback(
    callback: CallbackQuery,
    state: FSMContext,
    db_session: AsyncSession,
) -> None:
    data = await state.get_data()
    if "hold_id" in data and "client_id" in data:
        await BookingService(_settings()).release_hold(
            db_session,
            hold_id=UUID(data["hold_id"]),
            client_id=UUID(data["client_id"]),
        )
    await state.clear()
    await _edit_or_answer(
        callback,
        "Главное меню:",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "help")
async def help_callback(callback: CallbackQuery) -> None:
    await _edit_or_answer(
        callback,
        "Нажмите «Записаться», выберите услугу, мастера, дату и свободное время.\n"
        "До подтверждения выбранный слот удерживается 10 минут.",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "booking:start")
async def booking_start(
    callback: CallbackQuery,
    state: FSMContext,
    db_session: AsyncSession,
    business_id: UUID,
) -> None:
    services = list(
        (
            await db_session.scalars(
                select(Service)
                .where(
                    Service.business_id == business_id,
                    Service.is_active.is_(True),
                )
                .order_by(Service.name)
            )
        ).all()
    )
    if not services:
        await callback.answer("Услуги пока не настроены", show_alert=True)
        return
    await state.clear()
    await state.set_state(BookingStates.selecting_service)
    await _edit_or_answer(
        callback,
        "Выберите услугу:",
        reply_markup=services_keyboard(services),
    )
    await callback.answer()


@router.callback_query(BookingStates.selecting_service, F.data.startswith("service:"))
async def select_service(
    callback: CallbackQuery,
    state: FSMContext,
    db_session: AsyncSession,
    business_id: UUID,
) -> None:
    try:
        service_id = UUID(callback.data.split(":", 1)[1])
    except (AttributeError, ValueError):
        await callback.answer("Некорректная услуга", show_alert=True)
        return

    masters = list(
        (
            await db_session.scalars(
                select(Master)
                .join(MasterService, MasterService.master_id == Master.id)
                .where(
                    Master.business_id == business_id,
                    Master.is_active.is_(True),
                    MasterService.business_id == business_id,
                    MasterService.service_id == service_id,
                    MasterService.is_active.is_(True),
                )
                .order_by(Master.display_name)
            )
        ).all()
    )
    if not masters:
        await callback.answer("Для услуги нет доступных мастеров", show_alert=True)
        return
    await state.update_data(service_id=str(service_id))
    await state.set_state(BookingStates.selecting_master)
    await _edit_or_answer(
        callback,
        "Выберите мастера:",
        reply_markup=masters_keyboard(masters),
    )
    await callback.answer()


@router.callback_query(F.data == "booking:back:masters")
async def back_to_masters(
    callback: CallbackQuery,
    state: FSMContext,
    db_session: AsyncSession,
    business_id: UUID,
) -> None:
    data = await state.get_data()
    if "service_id" not in data:
        await booking_start(callback, state, db_session, business_id)
        return
    service_id = UUID(data["service_id"])
    masters = list(
        (
            await db_session.scalars(
                select(Master)
                .join(MasterService, MasterService.master_id == Master.id)
                .where(
                    Master.business_id == business_id,
                    MasterService.service_id == service_id,
                    Master.is_active.is_(True),
                    MasterService.is_active.is_(True),
                )
                .order_by(Master.display_name)
            )
        ).all()
    )
    await state.set_state(BookingStates.selecting_master)
    await _edit_or_answer(
        callback,
        "Выберите мастера:",
        reply_markup=masters_keyboard(masters),
    )
    await callback.answer()


@router.callback_query(BookingStates.selecting_master, F.data.startswith("master:"))
async def select_master(
    callback: CallbackQuery,
    state: FSMContext,
    db_session: AsyncSession,
    business_id: UUID,
) -> None:
    try:
        master_id = UUID(callback.data.split(":", 1)[1])
    except (AttributeError, ValueError):
        await callback.answer("Некорректный мастер", show_alert=True)
        return
    data = await state.get_data()
    if "service_id" not in data:
        await callback.answer("Сначала выберите услугу", show_alert=True)
        return
    available = await db_session.scalar(
        select(MasterService.id).where(
            MasterService.business_id == business_id,
            MasterService.master_id == master_id,
            MasterService.service_id == UUID(data["service_id"]),
            MasterService.is_active.is_(True),
        )
    )
    if available is None:
        await callback.answer("Мастер не оказывает эту услугу", show_alert=True)
        return
    await state.update_data(master_id=str(master_id))
    await _show_dates(callback, state, db_session, business_id)


@router.callback_query(F.data == "booking:back:dates")
async def back_to_dates(
    callback: CallbackQuery,
    state: FSMContext,
    db_session: AsyncSession,
    business_id: UUID,
) -> None:
    await _show_dates(callback, state, db_session, business_id)


@router.callback_query(BookingStates.selecting_date, F.data.startswith("date:"))
async def select_date(
    callback: CallbackQuery,
    state: FSMContext,
    db_session: AsyncSession,
    business_id: UUID,
) -> None:
    try:
        local_date = date.fromisoformat(callback.data.split(":", 1)[1])
        data = await state.get_data()
        service_id = UUID(data["service_id"])
        master_id = UUID(data["master_id"])
    except (AttributeError, KeyError, ValueError):
        await callback.answer("Начните выбор заново", show_alert=True)
        return

    try:
        slots = await AvailabilityService(_settings()).list_slots(
            db_session,
            business_id=business_id,
            master_id=master_id,
            service_id=service_id,
            local_date=local_date,
        )
    except BookingConfigurationError:
        await callback.answer("Расписание настроено некорректно", show_alert=True)
        return

    master = await db_session.get(Master, master_id)
    business = await db_session.get(Business, business_id)
    if master is None or business is None:
        await callback.answer("Мастер недоступен", show_alert=True)
        return
    timezone = ZoneInfo(master.timezone or business.timezone)
    await state.update_data(local_date=local_date.isoformat())
    if not slots:
        await state.set_state(BookingStates.selecting_date)
        await _edit_or_answer(
            callback,
            "На эту дату свободных окон нет. Выберите другую дату:",
            reply_markup=dates_keyboard(
                [
                    datetime.now(UTC).astimezone(timezone).date() + timedelta(days=offset)
                    for offset in range(_settings().booking_dates_shown)
                ]
            ),
        )
    else:
        await state.set_state(BookingStates.selecting_slot)
        await _edit_or_answer(
            callback,
            f"Свободное время на {local_date:%d.%m.%Y}:",
            reply_markup=slots_keyboard(slots, timezone),
        )
    await callback.answer()


@router.callback_query(BookingStates.selecting_slot, F.data.startswith("slot:"))
async def select_slot(
    callback: CallbackQuery,
    state: FSMContext,
    db_session: AsyncSession,
    business_id: UUID,
) -> None:
    if callback.from_user is None:
        return
    try:
        service_start = datetime.fromtimestamp(int(callback.data.split(":", 1)[1]), tz=UTC)
        data = await state.get_data()
        service_id = UUID(data["service_id"])
        master_id = UUID(data["master_id"])
        local_date = date.fromisoformat(data["local_date"])
    except (AttributeError, KeyError, TypeError, ValueError):
        await callback.answer("Начните выбор заново", show_alert=True)
        return

    user = await get_or_create_telegram_user(db_session, callback.from_user)
    try:
        hold = await BookingService(_settings()).create_hold(
            db_session,
            business_id=business_id,
            master_id=master_id,
            service_id=service_id,
            client_id=user.id,
            service_start=service_start,
            local_date=local_date,
        )
    except SlotUnavailableError:
        await callback.answer(
            "Это время уже занято. Выберите другое.",
            show_alert=True,
        )
        return

    await state.update_data(hold_id=str(hold.id), client_id=str(user.id))
    message = _message_from_callback(callback)
    if user.phone:
        await state.set_state(BookingStates.confirming)
        if message is not None:
            summary = await BookingService(_settings()).get_hold_summary(
                db_session, hold_id=hold.id, client_id=user.id
            )
            await message.edit_text(_format_hold(summary), reply_markup=confirmation_keyboard())
    else:
        await state.set_state(BookingStates.waiting_phone)
        if message is not None:
            await message.edit_text("Время удерживается 10 минут.")
            await message.answer(
                "Отправьте номер телефона для связи с мастером:",
                reply_markup=phone_keyboard(),
            )
    await callback.answer()


@router.message(BookingStates.waiting_phone, F.contact)
async def receive_contact(
    message: Message,
    state: FSMContext,
    db_session: AsyncSession,
) -> None:
    if message.from_user is None or message.contact is None:
        return
    if message.contact.user_id not in (None, message.from_user.id):
        await message.answer("Отправьте, пожалуйста, собственный номер телефона.")
        return
    data = await state.get_data()
    user = await db_session.get(TelegramUser, UUID(data["client_id"]))
    if user is None:
        await state.clear()
        return
    await set_user_phone(db_session, user, message.contact.phone_number)
    await message.answer("Телефон сохранен.", reply_markup=ReplyKeyboardRemove())
    await _show_confirmation(message, state, db_session)


@router.message(BookingStates.waiting_phone, F.text)
async def receive_phone_text(
    message: Message,
    state: FSMContext,
    db_session: AsyncSession,
) -> None:
    raw_phone = message.text or ""
    digits = re.sub(r"\D", "", raw_phone)
    if not 10 <= len(digits) <= 15:
        await message.answer("Введите телефон из 10-15 цифр или используйте кнопку ниже.")
        return
    normalized = f"+{digits}" if raw_phone.strip().startswith("+") else digits
    data = await state.get_data()
    user = await db_session.get(TelegramUser, UUID(data["client_id"]))
    if user is None:
        await state.clear()
        return
    await set_user_phone(db_session, user, normalized)
    await message.answer("Телефон сохранен.", reply_markup=ReplyKeyboardRemove())
    await _show_confirmation(message, state, db_session)


@router.callback_query(BookingStates.confirming, F.data == "booking:confirm")
async def confirm_booking(
    callback: CallbackQuery,
    state: FSMContext,
    db_session: AsyncSession,
) -> None:
    data = await state.get_data()
    try:
        summary = await BookingService(_settings()).confirm_hold(
            db_session,
            hold_id=UUID(data["hold_id"]),
            client_id=UUID(data["client_id"]),
        )
    except ClientPhoneRequiredError:
        await state.set_state(BookingStates.waiting_phone)
        await callback.answer("Нужен номер телефона", show_alert=True)
        message = _message_from_callback(callback)
        if message is not None:
            await message.answer(
                "Отправьте номер телефона для связи с мастером:",
                reply_markup=phone_keyboard(),
            )
        return
    except (
        KeyError,
        ValueError,
        HoldNotFoundError,
        HoldExpiredError,
    ):
        await state.clear()
        await callback.answer("Время удержания истекло", show_alert=True)
        await _edit_or_answer(
            callback,
            "Выбранное время больше не удерживается.",
            reply_markup=main_menu_keyboard(),
        )
        return

    await state.clear()
    status_text = (
        "Ожидает подтверждения мастером" if summary.status == "pending_approval" else "Подтверждена"
    )
    location = f"\nАдрес: <b>{escape(summary.location_name)}</b>" if summary.location_name else ""
    await _edit_or_answer(
        callback,
        "Запись создана!\n\n"
        f"Статус: <b>{status_text}</b>\n"
        f"Услуга: <b>{escape(summary.service_name)}</b>\n"
        f"Мастер: <b>{escape(summary.master_name)}</b>\n"
        f"Дата и время: <b>{summary.local_start:%d.%m.%Y %H:%M}</b>"
        f"{location}",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer("Запись подтверждена")


@router.callback_query(F.data == "booking:abort")
async def abort_booking(
    callback: CallbackQuery,
    state: FSMContext,
    db_session: AsyncSession,
) -> None:
    data = await state.get_data()
    if "hold_id" in data and "client_id" in data:
        await BookingService(_settings()).release_hold(
            db_session,
            hold_id=UUID(data["hold_id"]),
            client_id=UUID(data["client_id"]),
        )
    await state.clear()
    await _edit_or_answer(
        callback,
        "Запись отменена до подтверждения.",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "booking:mine")
async def my_bookings(
    callback: CallbackQuery,
    state: FSMContext,
    db_session: AsyncSession,
    business_id: UUID,
) -> None:
    user = await get_or_create_telegram_user(db_session, callback.from_user)
    appointments = await BookingService(_settings()).list_upcoming(
        db_session,
        business_id=business_id,
        client_id=user.id,
    )
    await state.clear()
    if not appointments:
        text = "У вас пока нет предстоящих записей."
    else:
        parts = ["Ваши предстоящие записи:"]
        for item in appointments:
            location = f", {escape(item.location_name)}" if item.location_name else ""
            parts.append(
                "\n"
                f"<b>{item.local_start:%d.%m.%Y %H:%M}</b>\n"
                f"{escape(item.service_name)} - {escape(item.master_name)}{location}"
            )
        text = "\n".join(parts)
    await _edit_or_answer(callback, text, reply_markup=main_menu_keyboard())
    await callback.answer()
