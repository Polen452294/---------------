from datetime import timedelta

from aiogram import Dispatcher
from aiogram.fsm.storage.redis import RedisStorage

from booking_bot.bot.handlers.booking import router as booking_router
from booking_bot.bot.handlers.common import router as common_router
from booking_bot.bot.handlers.master import router as master_router
from booking_bot.bot.handlers.specialist_setup import router as specialist_setup_router
from booking_bot.bot.middlewares import DatabaseSessionMiddleware
from booking_bot.config import get_settings

settings = get_settings()
storage = RedisStorage.from_url(
    settings.redis_url,
    state_ttl=timedelta(hours=2),
    data_ttl=timedelta(hours=2),
)
dispatcher = Dispatcher(storage=storage)
dispatcher.update.outer_middleware(DatabaseSessionMiddleware())
dispatcher.include_router(common_router)
dispatcher.include_router(master_router)
dispatcher.include_router(specialist_setup_router)
dispatcher.include_router(booking_router)
