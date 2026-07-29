from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from booking_bot.bot.handlers.master import _parse_price_minor
from booking_bot.bot.keyboards import master_service_actions_keyboard
from booking_bot.db.models import Business, Master, MasterService, Service, TelegramUser
from booking_bot.db.session import async_session_factory
from booking_bot.services.service_catalog import (
    DuplicateServiceNameError,
    InvalidServiceValueError,
    SpecialistServiceCatalog,
)
from booking_bot.services.specialist_setup import configure_specialist
from booking_bot.specialist_config import load_specialist_template

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_price_parser_accepts_russian_money_formats() -> None:
    assert _parse_price_minor("3500") == 350_000
    assert _parse_price_minor("3 500,50 ₽") == 350_050
    assert _parse_price_minor("-") is None
    with pytest.raises(InvalidServiceValueError):
        _parse_price_minor("три тысячи")


def test_service_action_callback_data_fits_telegram_limit() -> None:
    service = Service(
        id=uuid4(),
        business_id=uuid4(),
        name="Консультация",
        duration_minutes=60,
    )

    keyboard = master_service_actions_keyboard(service)

    callbacks = [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    ]
    assert callbacks
    assert all(len(callback.encode("utf-8")) <= 64 for callback in callbacks)


@pytest.mark.integration
async def test_owner_can_create_edit_publish_and_hide_service() -> None:
    catalog = SpecialistServiceCatalog()
    async with async_session_factory() as session:
        suffix = uuid4().hex[:12]
        business = Business(
            slug=f"catalog-{suffix}",
            name="Catalog Test",
            currency="RUB",
        )
        session.add(business)
        await session.flush()
        master = Master(
            business_id=business.id,
            display_name="Анна",
        )
        session.add(master)
        await session.flush()

        service = await catalog.create_service(
            session,
            business_id=business.id,
            master_id=master.id,
            name="Новая услуга",
        )
        assert service.is_owner_managed is True
        assert service.is_active is False
        assert service.duration_minutes == 60

        link = await session.scalar(
            select(MasterService).where(MasterService.service_id == service.id)
        )
        assert link is not None
        assert link.is_active is False

        service = await catalog.set_name(
            session,
            business_id=business.id,
            master_id=master.id,
            service_id=service.id,
            name="Большая консультация",
        )
        service = await catalog.set_description(
            session,
            business_id=business.id,
            master_id=master.id,
            service_id=service.id,
            description="Подробное обсуждение проекта",
        )
        service = await catalog.set_duration(
            session,
            business_id=business.id,
            master_id=master.id,
            service_id=service.id,
            duration_minutes=90,
        )
        service = await catalog.set_price(
            session,
            business_id=business.id,
            master_id=master.id,
            service_id=service.id,
            price_minor=450_000,
        )
        service = await catalog.set_buffers(
            session,
            business_id=business.id,
            master_id=master.id,
            service_id=service.id,
            buffer_before_minutes=15,
            buffer_after_minutes=30,
        )
        service = await catalog.toggle_approval(
            session,
            business_id=business.id,
            master_id=master.id,
            service_id=service.id,
        )
        service = await catalog.toggle_active(
            session,
            business_id=business.id,
            master_id=master.id,
            service_id=service.id,
        )

        assert service.name == "Большая консультация"
        assert service.description == "Подробное обсуждение проекта"
        assert service.duration_minutes == 90
        assert service.price_minor == 450_000
        assert service.buffer_before_minutes == 15
        assert service.buffer_after_minutes == 30
        assert service.requires_approval is True
        assert service.is_active is True
        assert link.is_active is True

        with pytest.raises(DuplicateServiceNameError):
            await catalog.create_service(
                session,
                business_id=business.id,
                master_id=master.id,
                name="большая консультация",
            )

        service = await catalog.toggle_active(
            session,
            business_id=business.id,
            master_id=master.id,
            service_id=service.id,
        )
        assert service.is_active is False
        assert link.is_active is False
        assert await session.get(Service, service.id) is service
        await session.rollback()


@pytest.mark.integration
async def test_owner_service_changes_survive_template_reconfiguration() -> None:
    catalog = SpecialistServiceCatalog()
    template = load_specialist_template(PROJECT_ROOT / "specialist.toml")
    async with async_session_factory() as session:
        deployment = await configure_specialist(session, template)
        service = await session.scalar(
            select(Service).where(
                Service.business_id == deployment.business_id,
                Service.config_key == template.services[0].key,
            )
        )
        assert service is not None
        master = await session.get(Master, deployment.master_id)
        assert master is not None
        if master.user_id is None:
            owner = TelegramUser(telegram_user_id=-(uuid4().int % 2_000_000_000))
            session.add(owner)
            await session.flush()
            master.user_id = owner.id
        master.display_name = "Имя из кабинета"
        master.bio = "Описание из кабинета"

        await catalog.set_duration(
            session,
            business_id=deployment.business_id,
            master_id=deployment.master_id,
            service_id=service.id,
            duration_minutes=95,
        )
        await configure_specialist(session, template)
        await session.refresh(service)

        assert service.duration_minutes == 95
        assert service.is_owner_managed is True
        assert master.display_name == "Имя из кабинета"
        assert master.bio == "Описание из кабинета"
        await session.rollback()
