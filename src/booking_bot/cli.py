import argparse
import asyncio
import logging
import secrets
from datetime import time
from uuid import UUID

from aiogram.types import BotCommand
from sqlalchemy import select

from booking_bot.bot import dispatcher
from booking_bot.bot.factory import create_telegram_bot
from booking_bot.config import get_settings
from booking_bot.db.models import (
    BotInstallation,
    Business,
    Location,
    Master,
    MasterService,
    Service,
    WorkingRule,
)
from booking_bot.db.session import async_session_factory, engine
from booking_bot.services.master_access import create_master_invite
from booking_bot.services.notification_delivery import NotificationDeliveryService
from booking_bot.services.token_cipher import BotTokenCipher, hash_webhook_secret


async def seed_demo() -> None:
    async with async_session_factory() as session:
        existing = await session.scalar(select(Business).where(Business.slug == "demo"))
        if existing is not None:
            print(f"Demo business already exists: {existing.id}")
            return

        business = Business(
            slug="demo",
            name="Demo Tattoo Studio",
            timezone="Europe/Moscow",
            locale="ru",
            currency="RUB",
        )
        session.add(business)
        await session.flush()

        location = Location(
            business_id=business.id,
            name="Студия",
            address="Москва, тестовый адрес",
            timezone="Europe/Moscow",
        )
        master = Master(
            business_id=business.id,
            display_name="Анна",
            bio="Тату-мастер",
            timezone="Europe/Moscow",
        )
        session.add_all([location, master])
        await session.flush()

        consultation = Service(
            business_id=business.id,
            name="Консультация",
            description="Обсуждение идеи, размера и размещения татуировки",
            duration_minutes=60,
            buffer_after_minutes=15,
            price_minor=200_000,
            currency="RUB",
        )
        tattoo_session = Service(
            business_id=business.id,
            name="Сеанс татуировки",
            description="Тестовая услуга продолжительностью три часа",
            duration_minutes=180,
            buffer_before_minutes=15,
            buffer_after_minutes=30,
            price_minor=1_000_000,
            currency="RUB",
            requires_approval=True,
        )
        session.add_all([consultation, tattoo_session])
        await session.flush()
        session.add_all(
            [
                MasterService(
                    business_id=business.id,
                    master_id=master.id,
                    service_id=consultation.id,
                ),
                MasterService(
                    business_id=business.id,
                    master_id=master.id,
                    service_id=tattoo_session.id,
                ),
            ]
        )

        for weekday in range(5):
            session.add(
                WorkingRule(
                    business_id=business.id,
                    master_id=master.id,
                    location_id=location.id,
                    weekday=weekday,
                    start_time=time(10, 0),
                    end_time=time(19, 0),
                )
            )
        session.add(
            WorkingRule(
                business_id=business.id,
                master_id=master.id,
                location_id=location.id,
                weekday=5,
                start_time=time(11, 0),
                end_time=time(17, 0),
            )
        )
        await session.commit()
        print(f"Demo business created: {business.id}")


async def register_bot(args: argparse.Namespace) -> None:
    settings = get_settings()
    if settings.telegram_bot_token is None:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
    token = settings.telegram_bot_token.get_secret_value()
    encryption_key = (
        settings.bot_token_encryption_key.get_secret_value()
        if settings.bot_token_encryption_key
        else None
    )
    cipher = BotTokenCipher(encryption_key)
    header_secret = (
        settings.telegram_webhook_header_secret.get_secret_value()
        if settings.telegram_webhook_header_secret
        else secrets.token_urlsafe(32)
    )

    async with async_session_factory() as session:
        business = await session.scalar(select(Business).where(Business.slug == args.business))
        if business is None:
            raise RuntimeError(f"Business not found: {args.business}")
        installation = await session.scalar(
            select(BotInstallation).where(BotInstallation.telegram_bot_id == args.bot_id)
        )
        if installation is None:
            installation = BotInstallation(
                business_id=business.id,
                telegram_bot_id=args.bot_id,
                webhook_path_secret=secrets.token_urlsafe(32),
                webhook_header_secret_hash=hash_webhook_secret(header_secret),
                token_ciphertext=cipher.encrypt(token),
            )
            session.add(installation)
        else:
            installation.business_id = business.id
            installation.username = args.username
            installation.token_ciphertext = cipher.encrypt(token)
            installation.webhook_header_secret_hash = hash_webhook_secret(header_secret)
            installation.is_active = True
        installation.username = args.username
        await session.commit()

        base_url = (
            str(settings.telegram_webhook_base_url).rstrip("/")
            if settings.telegram_webhook_base_url
            else "https://YOUR-DOMAIN"
        )
        print(
            f"Webhook URL: {base_url}/api/v1/webhooks/telegram/{installation.webhook_path_secret}"
        )
        print(f"Webhook header secret: {header_secret}")


async def run_polling(args: argparse.Namespace) -> None:
    settings = get_settings()
    if settings.telegram_bot_token is None:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")

    async with async_session_factory() as session:
        business = await session.scalar(select(Business).where(Business.slug == args.business))
        if business is None:
            raise RuntimeError(f"Business not found: {args.business}")
        business_id = business.id

    bot = create_telegram_bot(settings.telegram_bot_token.get_secret_value(), settings)
    try:
        bot_info = await bot.get_me()
        await bot.delete_webhook(drop_pending_updates=False)
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Открыть меню записи"),
                BotCommand(command="help", description="Помощь"),
            ]
        )
        print(
            f"Polling started for @{bot_info.username} (business: {args.business})",
            flush=True,
        )
        print("Press Ctrl+C to stop.", flush=True)
        await dispatcher.start_polling(
            bot,
            business_id=business_id,
            bot_installation_id=None,
            close_bot_session=False,
        )
    finally:
        await dispatcher.storage.close()
        await bot.session.close()


async def create_master_invite_link(args: argparse.Namespace) -> None:
    settings = get_settings()
    if settings.telegram_bot_token is None:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")

    async with async_session_factory() as session:
        business = await session.scalar(select(Business).where(Business.slug == args.business))
        if business is None:
            raise RuntimeError(f"Business not found: {args.business}")
        try:
            master_id = UUID(args.master)
        except ValueError:
            master_id = await session.scalar(
                select(Master.id).where(
                    Master.business_id == business.id,
                    Master.display_name == args.master,
                )
            )
        if master_id is None:
            raise RuntimeError(f"Master not found: {args.master}")
        invite = await create_master_invite(
            session,
            business_id=business.id,
            master_id=master_id,
        )
        await session.commit()

    bot = create_telegram_bot(settings.telegram_bot_token.get_secret_value(), settings)
    try:
        bot_info = await bot.get_me()
    finally:
        await bot.session.close()
    print(f"Master: {invite.master_name}")
    print(f"Invite expires at: {invite.expires_at.isoformat()}")
    print(f"Invite link: https://t.me/{bot_info.username}?start=master_{invite.token}")


async def run_notification_worker(args: argparse.Namespace) -> None:
    settings = get_settings()
    if settings.telegram_bot_token is None:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
    async with async_session_factory() as session:
        business = await session.scalar(select(Business).where(Business.slug == args.business))
        if business is None:
            raise RuntimeError(f"Business not found: {args.business}")
        business_id = business.id

    bot = create_telegram_bot(settings.telegram_bot_token.get_secret_value(), settings)
    try:
        bot_info = await bot.get_me()
        service = NotificationDeliveryService(settings)
        print(
            f"Notification worker started for @{bot_info.username} (business: {args.business})",
            flush=True,
        )
        if args.once:
            processed = await service.run_once(bot, business_id=business_id)
            print(f"Processed jobs: {processed}", flush=True)
        else:
            print("Press Ctrl+C to stop.", flush=True)
            await service.run_forever(bot, business_id=business_id)
    finally:
        await bot.session.close()


async def async_main(args: argparse.Namespace) -> None:
    try:
        if args.command == "seed-demo":
            await seed_demo()
        elif args.command == "register-bot":
            await register_bot(args)
        elif args.command == "run-polling":
            await run_polling(args)
        elif args.command == "create-master-invite":
            await create_master_invite_link(args)
        elif args.command == "run-worker":
            await run_notification_worker(args)
    finally:
        await engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="booking-admin")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("seed-demo", help="Create a demo business and schedule")

    register = subparsers.add_parser(
        "register-bot", help="Encrypt and attach a Telegram bot to a business"
    )
    register.add_argument("--business", default="demo")
    register.add_argument("--bot-id", type=int, required=True)
    register.add_argument("--username", required=True)

    polling = subparsers.add_parser(
        "run-polling", help="Run a configured Telegram bot locally without a webhook"
    )
    polling.add_argument("--business", default="demo")

    invite = subparsers.add_parser(
        "create-master-invite",
        help="Create a one-time Telegram link for a master",
    )
    invite.add_argument("--business", default="demo")
    invite.add_argument(
        "--master",
        required=True,
        help="Master UUID or exact display name",
    )

    worker = subparsers.add_parser(
        "run-worker",
        help="Deliver due Telegram notification jobs",
    )
    worker.add_argument("--business", default="demo")
    worker.add_argument("--once", action="store_true")
    return parser


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(async_main(build_parser().parse_args()))


if __name__ == "__main__":
    main()
