from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from booking_bot.bot import dispatcher
from booking_bot.bot.factory import create_telegram_bot
from booking_bot.config import Settings
from booking_bot.db.models import BotInstallation
from booking_bot.services.token_cipher import BotTokenCipher, webhook_secret_matches


class BotInstallationNotFoundError(LookupError):
    pass


class InvalidWebhookSecretError(PermissionError):
    pass


class TelegramWebhookService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        encryption_key = (
            settings.bot_token_encryption_key.get_secret_value()
            if settings.bot_token_encryption_key
            else None
        )
        self._cipher = BotTokenCipher(encryption_key)

    async def process(
        self,
        *,
        webhook_path_secret: str,
        webhook_header_secret: str | None,
        payload: dict[str, Any],
        session: AsyncSession,
    ) -> None:
        installation = await session.scalar(
            select(BotInstallation).where(
                BotInstallation.webhook_path_secret == webhook_path_secret,
                BotInstallation.is_active.is_(True),
            )
        )
        if installation is None:
            raise BotInstallationNotFoundError
        if not webhook_secret_matches(
            webhook_header_secret, installation.webhook_header_secret_hash
        ):
            raise InvalidWebhookSecretError

        token = self._cipher.decrypt(installation.token_ciphertext)
        bot = create_telegram_bot(token, self._settings)
        try:
            await dispatcher.feed_raw_update(
                bot,
                payload,
                business_id=installation.business_id,
                bot_installation_id=installation.id,
                db_session=session,
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await bot.session.close()
