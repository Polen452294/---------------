from fastapi import APIRouter

from booking_bot.api.routes import health, telegram

api_router = APIRouter()
api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(telegram.router, prefix="/webhooks/telegram", tags=["telegram"])
