from uuid import UUID

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from booking_bot.bot.keyboards import main_menu_keyboard, master_menu_keyboard
from booking_bot.config import get_settings
from booking_bot.services.bookings import BookingService
from booking_bot.services.master_access import (
    InvalidMasterInviteError,
    MasterAlreadyLinkedError,
    get_master_for_user,
    redeem_master_invite,
)
from booking_bot.services.users import get_or_create_telegram_user
from booking_bot.specialist_config import get_specialist_template

router = Router(name="common")


@router.message(CommandStart())
async def start_handler(
    message: Message,
    business_id: UUID,
    db_session: AsyncSession,
    state: FSMContext,
) -> None:
    if message.from_user is None:
        return
    data = await state.get_data()
    try:
        if "hold_id" in data and "client_id" in data:
            await BookingService(get_settings()).release_hold(
                db_session,
                hold_id=UUID(data["hold_id"]),
                client_id=UUID(data["client_id"]),
            )
    except ValueError:
        pass
    await state.clear()
    user = await get_or_create_telegram_user(db_session, message.from_user)
    payload = ""
    if message.text:
        command_parts = message.text.split(maxsplit=1)
        payload = command_parts[1].strip() if len(command_parts) == 2 else ""
    if payload.startswith("master_"):
        try:
            master = await redeem_master_invite(
                db_session,
                business_id=business_id,
                token=payload.removeprefix("master_"),
                user=user,
            )
        except InvalidMasterInviteError:
            await message.answer(
                "Ссылка приглашения недействительна или уже использована.",
                reply_markup=main_menu_keyboard(),
            )
            return
        except MasterAlreadyLinkedError:
            await message.answer(
                "Этот профиль специалиста уже привязан к другому пользователю.",
                reply_markup=main_menu_keyboard(),
            )
            return
        await message.answer(
            f"Профиль специалиста <b>{master.display_name}</b> успешно привязан.",
            reply_markup=master_menu_keyboard(),
        )
        return

    master = await get_master_for_user(
        db_session,
        business_id=business_id,
        user_id=user.id,
    )
    await message.answer(
        get_specialist_template().text(
            "welcome",
            "Здравствуйте! Здесь можно выбрать услугу, дату и свободное время.",
        ),
        reply_markup=main_menu_keyboard(master_access=master is not None),
    )


@router.message(Command("help"))
async def help_handler(message: Message) -> None:
    await message.answer(
        get_specialist_template().text(
            "help",
            "Используйте /start, чтобы открыть меню. "
            "Выбранное время удерживается 10 минут до подтверждения.",
        ),
        reply_markup=main_menu_keyboard(),
    )
