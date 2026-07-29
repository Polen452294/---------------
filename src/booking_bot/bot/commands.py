from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeChat


def client_commands() -> list[BotCommand]:
    return [
        BotCommand(command="start", description="Открыть меню записи"),
        BotCommand(command="help", description="Помощь"),
    ]


def master_commands() -> list[BotCommand]:
    return [
        BotCommand(command="cabinet", description="Открыть кабинет специалиста"),
        *client_commands(),
    ]


async def set_client_commands(bot: Bot) -> None:
    await bot.set_my_commands(client_commands())


async def set_master_commands(bot: Bot, *, chat_id: int) -> None:
    await bot.set_my_commands(
        master_commands(),
        scope=BotCommandScopeChat(chat_id=chat_id),
    )
