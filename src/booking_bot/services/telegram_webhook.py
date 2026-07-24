import hmac
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from booking_bot.bot import dispatcher
from booking_bot.bot.factory import create_telegram_bot
from booking_bot.config import Settings
from booking_bot.services.specialist_context import (
    SpecialistNotConfiguredError,
    get_specialist_context,
)


class InvalidWebhookSecretError(PermissionError):
    pass


class TelegramBotNotConfiguredError(RuntimeError):
    pass


class TelegramWebhookService:
    """Deliver updates to the only Telegram bot configured for this deployment."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def process(
        self,
        *,
        webhook_header_secret: str | None,
        payload: dict[str, Any],
        session: AsyncSession,
    ) -> None:
        expected_secret = (
            self._settings.telegram_webhook_header_secret.get_secret_value()
            if self._settings.telegram_webhook_header_secret
            else None
        )
        token = (
            self._settings.telegram_bot_token.get_secret_value()
            if self._settings.telegram_bot_token
            else None
        )
        if not expected_secret or not token:
            raise TelegramBotNotConfiguredError
        if webhook_header_secret is None or not hmac.compare_digest(
            webhook_header_secret,
            expected_secret,
        ):
            raise InvalidWebhookSecretError

        try:
            context = await get_specialist_context(session)
        except SpecialistNotConfiguredError as exc:
            raise TelegramBotNotConfiguredError from exc

        bot = create_telegram_bot(token, self._settings)
        try:
            await dispatcher.feed_raw_update(
                bot,
                payload,
                business_id=context.business_id,
                specialist_master_id=context.master_id,
                db_session=session,
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await bot.session.close()
