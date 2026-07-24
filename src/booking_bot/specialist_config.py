import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from booking_bot.config import get_settings


class SpecialistConfigError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ProfileConfig:
    slug: str
    brand_name: str
    specialist_name: str
    specialist_role: str
    bio: str
    timezone: str
    locale: str
    currency: str


@dataclass(frozen=True, slots=True)
class LocationConfig:
    name: str
    address: str


@dataclass(frozen=True, slots=True)
class ServiceConfig:
    key: str
    name: str
    description: str
    duration_minutes: int
    buffer_before_minutes: int
    buffer_after_minutes: int
    price_minor: int | None
    requires_approval: bool


@dataclass(frozen=True, slots=True)
class SpecialistTemplate:
    profile: ProfileConfig
    location: LocationConfig
    services: tuple[ServiceConfig, ...]
    schedule: dict[str, str]
    texts: dict[str, str] = field(default_factory=dict)
    buttons: dict[str, str] = field(default_factory=dict)

    def text(self, key: str, default: str, **values: Any) -> str:
        template = self.texts.get(key, default)
        context = {
            "brand_name": self.profile.brand_name,
            "specialist_name": self.profile.specialist_name,
            "specialist_role": self.profile.specialist_role,
            **values,
        }
        return template.format_map(context)

    def button(self, key: str, default: str) -> str:
        template = self.buttons.get(key, default)
        return template.format_map(
            {
                "brand_name": self.profile.brand_name,
                "specialist_name": self.profile.specialist_name,
                "specialist_role": self.profile.specialist_role,
            }
        )


def load_specialist_template(path: str | Path) -> SpecialistTemplate:
    config_path = Path(path)
    if not config_path.is_absolute():
        config_path = Path.cwd() / config_path
    if not config_path.is_file():
        raise SpecialistConfigError(f"Specialist config not found: {config_path}")
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        profile = ProfileConfig(**data["profile"])
        location = LocationConfig(**data["location"])
        services = tuple(ServiceConfig(**item) for item in data.get("services", []))
        schedule = dict(data.get("schedule", {}))
    except (KeyError, TypeError, ValueError, tomllib.TOMLDecodeError) as exc:
        raise SpecialistConfigError(f"Invalid specialist config: {config_path}") from exc
    if not services:
        raise SpecialistConfigError("At least one service must be configured")
    if len({service.key for service in services}) != len(services):
        raise SpecialistConfigError("Service keys must be unique")
    return SpecialistTemplate(
        profile=profile,
        location=location,
        services=services,
        schedule=schedule,
        texts=dict(data.get("texts", {})),
        buttons=dict(data.get("buttons", {})),
    )


def get_specialist_template() -> SpecialistTemplate:
    return load_specialist_template(get_settings().specialist_config_path)
