import argparse
import asyncio
import logging

from aiogram.types import BotCommand

from booking_bot.bot import dispatcher
from booking_bot.bot.factory import create_telegram_bot
from booking_bot.config import get_settings
from booking_bot.db.session import async_session_factory, engine
from booking_bot.services.master_access import create_master_invite
from booking_bot.services.notification_delivery import NotificationDeliveryService
from booking_bot.services.specialist_context import get_specialist_context
from booking_bot.services.specialist_setup import configure_specialist
from booking_bot.specialist_config import (
    get_specialist_template,
    load_specialist_template,
)


async def configure_copy(args: argparse.Namespace) -> None:
    template = load_specialist_template(args.config) if args.config else get_specialist_template()
    async with async_session_factory() as session:
        deployment = await configure_specialist(
            session,
            template,
            replace_schedule=args.reset_schedule,
        )
        await session.commit()
    print(f"Configured specialist: {template.profile.specialist_name}")
    print(f"Brand: {template.profile.brand_name}")
    print(f"Profile id: {deployment.id}")


async def run_polling(_: argparse.Namespace) -> None:
    settings = get_settings()
    template = get_specialist_template()
    if settings.telegram_bot_token is None:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")

    async with async_session_factory() as session:
        context = await get_specialist_context(session)

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
            f"Polling started for @{bot_info.username} "
            f"(specialist: {template.profile.specialist_name})",
            flush=True,
        )
        print("Press Ctrl+C to stop.", flush=True)
        await dispatcher.start_polling(
            bot,
            business_id=context.business_id,
            specialist_master_id=context.master_id,
            close_bot_session=False,
        )
    finally:
        await dispatcher.storage.close()
        await bot.session.close()


async def create_master_invite_link(_: argparse.Namespace) -> None:
    settings = get_settings()
    if settings.telegram_bot_token is None:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")

    async with async_session_factory() as session:
        context = await get_specialist_context(session)
        invite = await create_master_invite(
            session,
            business_id=context.business_id,
            master_id=context.master_id,
        )
        await session.commit()

    bot = create_telegram_bot(settings.telegram_bot_token.get_secret_value(), settings)
    try:
        bot_info = await bot.get_me()
    finally:
        await bot.session.close()
    print(f"Specialist: {invite.master_name}")
    print(f"Invite expires at: {invite.expires_at.isoformat()}")
    print(f"Invite link: https://t.me/{bot_info.username}?start=master_{invite.token}")


async def run_notification_worker(args: argparse.Namespace) -> None:
    settings = get_settings()
    template = get_specialist_template()
    if settings.telegram_bot_token is None:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
    async with async_session_factory() as session:
        context = await get_specialist_context(session)

    bot = create_telegram_bot(settings.telegram_bot_token.get_secret_value(), settings)
    try:
        bot_info = await bot.get_me()
        service = NotificationDeliveryService(settings)
        print(
            f"Notification worker started for @{bot_info.username} "
            f"(specialist: {template.profile.specialist_name})",
            flush=True,
        )
        if args.once:
            processed = await service.run_once(bot, business_id=context.business_id)
            print(f"Processed jobs: {processed}", flush=True)
        else:
            print("Press Ctrl+C to stop.", flush=True)
            await service.run_forever(bot, business_id=context.business_id)
    finally:
        await bot.session.close()


async def set_webhook(_: argparse.Namespace) -> None:
    settings = get_settings()
    if settings.telegram_bot_token is None:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
    if settings.telegram_webhook_base_url is None:
        raise RuntimeError("TELEGRAM_WEBHOOK_BASE_URL is not configured")
    if settings.telegram_webhook_header_secret is None:
        raise RuntimeError("TELEGRAM_WEBHOOK_HEADER_SECRET is not configured")

    url = f"{str(settings.telegram_webhook_base_url).rstrip('/')}/api/v1/webhooks/telegram"
    bot = create_telegram_bot(settings.telegram_bot_token.get_secret_value(), settings)
    try:
        await bot.set_webhook(
            url=url,
            secret_token=settings.telegram_webhook_header_secret.get_secret_value(),
            drop_pending_updates=False,
        )
    finally:
        await bot.session.close()
    print(f"Webhook configured: {url}")


async def async_main(args: argparse.Namespace) -> None:
    try:
        if args.command == "configure":
            await configure_copy(args)
        elif args.command == "run-polling":
            await run_polling(args)
        elif args.command == "create-master-invite":
            await create_master_invite_link(args)
        elif args.command == "run-worker":
            await run_notification_worker(args)
        elif args.command == "set-webhook":
            await set_webhook(args)
    finally:
        await engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="booking-admin")
    subparsers = parser.add_subparsers(dest="command", required=True)

    configure = subparsers.add_parser(
        "configure",
        help="Apply specialist.toml to this bot copy",
    )
    configure.add_argument("--config", help="Alternative TOML config path")
    configure.add_argument(
        "--reset-schedule",
        action="store_true",
        help="Replace working hours with the schedule from TOML",
    )
    subparsers.add_parser(
        "run-polling",
        help="Run this specialist bot locally without a webhook",
    )
    subparsers.add_parser(
        "create-master-invite",
        help="Create a one-time owner link for this specialist",
    )
    worker = subparsers.add_parser(
        "run-worker",
        help="Deliver due Telegram notification jobs",
    )
    worker.add_argument("--once", action="store_true")
    subparsers.add_parser(
        "set-webhook",
        help="Configure the single production webhook",
    )
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
