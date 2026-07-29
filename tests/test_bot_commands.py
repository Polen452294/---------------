from aiogram.types import BotCommandScopeChat

from booking_bot.bot.commands import (
    client_commands,
    master_commands,
    set_master_commands,
)


def test_master_menu_adds_cabinet_without_exposing_it_to_clients() -> None:
    assert [command.command for command in client_commands()] == ["start", "help"]
    assert [command.command for command in master_commands()] == ["cabinet", "start", "help"]


async def test_master_menu_uses_chat_specific_scope() -> None:
    class BotStub:
        commands = None
        scope = None

        async def set_my_commands(self, commands, *, scope=None):
            self.commands = commands
            self.scope = scope

    bot = BotStub()
    await set_master_commands(bot, chat_id=123456)

    assert [command.command for command in bot.commands] == ["cabinet", "start", "help"]
    assert isinstance(bot.scope, BotCommandScopeChat)
    assert bot.scope.chat_id == 123456
