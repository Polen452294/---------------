from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Path, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from booking_bot.config import Settings, get_settings
from booking_bot.db.session import get_session
from booking_bot.services.telegram_webhook import (
    BotInstallationNotFoundError,
    InvalidWebhookSecretError,
    TelegramWebhookService,
)
from booking_bot.services.token_cipher import TokenCipherConfigurationError

router = APIRouter()


@router.post("/{webhook_path_secret}", status_code=status.HTTP_200_OK)
async def receive_update(
    payload: dict[str, Any],
    webhook_path_secret: Annotated[str, Path(min_length=24, max_length=64)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    webhook_header_secret: Annotated[
        str | None,
        Header(alias="X-Telegram-Bot-Api-Secret-Token"),
    ] = None,
) -> dict[str, bool]:
    try:
        service = TelegramWebhookService(settings)
        await service.process(
            webhook_path_secret=webhook_path_secret,
            webhook_header_secret=webhook_header_secret,
            payload=payload,
            session=session,
        )
    except BotInstallationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot not found") from exc
    except InvalidWebhookSecretError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid secret") from exc
    except TokenCipherConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Bot token encryption is not configured",
        ) from exc
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid Telegram update",
        ) from exc
    return {"ok": True}
