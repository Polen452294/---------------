from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from booking_bot.config import Settings, get_settings
from booking_bot.db.session import get_session
from booking_bot.services.telegram_webhook import (
    InvalidWebhookSecretError,
    TelegramBotNotConfiguredError,
    TelegramWebhookService,
)

router = APIRouter()


@router.post("", status_code=status.HTTP_200_OK)
async def receive_update(
    payload: dict[str, Any],
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
            webhook_header_secret=webhook_header_secret,
            payload=payload,
            session=session,
        )
    except InvalidWebhookSecretError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid secret",
        ) from exc
    except TelegramBotNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram bot is not configured",
        ) from exc
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid Telegram update",
        ) from exc
    return {"ok": True}
