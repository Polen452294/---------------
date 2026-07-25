from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from booking_bot.db.models import Appointment, Business, CalendarEntry, Master, TelegramUser
from booking_bot.domain.enums import AppointmentStatus

ACTIVE_APPOINTMENT_STATUSES = {
    AppointmentStatus.PENDING_APPROVAL.value,
    AppointmentStatus.PENDING_PAYMENT.value,
    AppointmentStatus.CONFIRMED.value,
}
CANCELLED_APPOINTMENT_STATUSES = {
    AppointmentStatus.CANCELLED_BY_CLIENT.value,
    AppointmentStatus.CANCELLED_BY_MASTER.value,
}
RUSSIAN_MONTHS = (
    "январь",
    "февраль",
    "март",
    "апрель",
    "май",
    "июнь",
    "июль",
    "август",
    "сентябрь",
    "октябрь",
    "ноябрь",
    "декабрь",
)


class AnalyticsError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AnalyticsPeriod:
    key: str
    label: str
    starts_at: datetime
    ends_at: datetime
    local_start_date: date
    local_end_date: date


@dataclass(frozen=True, slots=True)
class PopularService:
    name: str
    booking_count: int


@dataclass(frozen=True, slots=True)
class MasterAnalytics:
    period: AnalyticsPeriod
    currency: str
    total_bookings: int
    active_bookings: int
    completed_bookings: int
    cancelled_by_client: int
    cancelled_by_master: int
    no_shows: int
    distinct_clients: int
    manual_bookings: int
    completed_revenue_minor: int
    active_value_minor: int
    average_completed_price_minor: int | None
    popular_services: tuple[PopularService, ...]


def build_analytics_period(
    *,
    mode: str,
    timezone: ZoneInfo,
    now: datetime | None = None,
) -> AnalyticsPeriod:
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    local_now = now.astimezone(timezone)
    today = local_now.date()

    if mode == "current_month":
        local_start = today.replace(day=1)
        local_end = _next_month(local_start)
        label = f"{RUSSIAN_MONTHS[local_start.month - 1]} {local_start.year}"
    elif mode == "previous_month":
        local_end = today.replace(day=1)
        local_start = (local_end - timedelta(days=1)).replace(day=1)
        label = f"{RUSSIAN_MONTHS[local_start.month - 1]} {local_start.year}"
    elif mode == "7_days":
        local_start = today - timedelta(days=6)
        local_end = today + timedelta(days=1)
        label = f"{local_start:%d.%m.%Y}–{today:%d.%m.%Y}"
    elif mode == "30_days":
        local_start = today - timedelta(days=29)
        local_end = today + timedelta(days=1)
        label = f"{local_start:%d.%m.%Y}–{today:%d.%m.%Y}"
    else:
        raise ValueError(f"Unknown analytics period: {mode}")

    return AnalyticsPeriod(
        key=mode,
        label=label,
        starts_at=datetime.combine(local_start, time.min, timezone).astimezone(UTC),
        ends_at=datetime.combine(local_end, time.min, timezone).astimezone(UTC),
        local_start_date=local_start,
        local_end_date=local_end,
    )


class MasterAnalyticsService:
    async def build_report(
        self,
        session: AsyncSession,
        *,
        business_id: UUID,
        master: Master,
        period: AnalyticsPeriod,
    ) -> MasterAnalytics:
        business = await session.get(Business, business_id)
        if business is None or master.business_id != business_id:
            raise AnalyticsError("Specialist profile not found")

        rows = (
            await session.execute(
                select(Appointment, TelegramUser.telegram_user_id)
                .join(CalendarEntry, CalendarEntry.id == Appointment.calendar_entry_id)
                .join(TelegramUser, TelegramUser.id == Appointment.client_id)
                .where(
                    Appointment.business_id == business_id,
                    CalendarEntry.master_id == master.id,
                    Appointment.service_starts_at >= period.starts_at,
                    Appointment.service_starts_at < period.ends_at,
                )
                .order_by(Appointment.service_starts_at)
            )
        ).all()

        status_counts: Counter[str] = Counter()
        service_counts: Counter[str] = Counter()
        client_ids: set[UUID] = set()
        manual_bookings = 0
        completed_revenue_minor = 0
        active_value_minor = 0
        priced_completed_count = 0

        for appointment, telegram_user_id in rows:
            status_counts[appointment.status] += 1
            client_ids.add(appointment.client_id)
            manual_bookings += int(telegram_user_id is None)
            if appointment.status not in CANCELLED_APPOINTMENT_STATUSES | {
                AppointmentStatus.NO_SHOW.value
            }:
                service_counts[appointment.service_name_snapshot] += 1
            if appointment.status == AppointmentStatus.COMPLETED.value:
                if appointment.price_minor is not None:
                    completed_revenue_minor += appointment.price_minor
                    priced_completed_count += 1
            elif appointment.status in ACTIVE_APPOINTMENT_STATUSES:
                active_value_minor += appointment.price_minor or 0

        popular_services = tuple(
            PopularService(name=name, booking_count=count)
            for name, count in sorted(
                service_counts.items(),
                key=lambda item: (-item[1], item[0].casefold()),
            )[:5]
        )
        average_completed = (
            completed_revenue_minor // priced_completed_count if priced_completed_count else None
        )
        return MasterAnalytics(
            period=period,
            currency=business.currency,
            total_bookings=len(rows),
            active_bookings=sum(status_counts[status] for status in ACTIVE_APPOINTMENT_STATUSES),
            completed_bookings=status_counts[AppointmentStatus.COMPLETED.value],
            cancelled_by_client=status_counts[AppointmentStatus.CANCELLED_BY_CLIENT.value],
            cancelled_by_master=status_counts[AppointmentStatus.CANCELLED_BY_MASTER.value],
            no_shows=status_counts[AppointmentStatus.NO_SHOW.value],
            distinct_clients=len(client_ids),
            manual_bookings=manual_bookings,
            completed_revenue_minor=completed_revenue_minor,
            active_value_minor=active_value_minor,
            average_completed_price_minor=average_completed,
            popular_services=popular_services,
        )


def _next_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)
