from datetime import time
from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_times(value: str | list) -> list[time]:
    if isinstance(value, str):
        value = [v for v in (p.strip() for p in value.split(",")) if v]
    return [v if isinstance(v, time) else time.fromisoformat(v) for v in value]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Pilates Studio API"
    environment: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"

    database_url: str = "postgresql+psycopg://pilates:pilates@localhost:5432/pilates"
    redis_url: str = "redis://localhost:6379/0"
    # Pool por proceso: con WEB_CONCURRENCY workers de uvicorn se abren hasta
    # workers × (pool_size + max_overflow) conexiones (Postgres admite 100 por defecto).
    db_pool_size: int = 5
    db_max_overflow: int = 10

    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 60 * 12

    timezone: str = "America/Argentina/Buenos_Aires"

    # Grilla
    class_capacity: int = 4
    class_duration_minutes: int = 60
    morning_slots: str = "08:00,09:00,10:00,11:00"
    afternoon_slots: str = "15:00,16:00,17:00"

    # Reglas de negocio
    booking_opening_time: time = time(20, 0)
    allow_same_day_booking: bool = True
    min_booking_advance_minutes: int = 0
    cancel_min_notice_minutes: int = 60
    max_bookings_per_day: int = 2
    max_bookings_per_block: int = 1

    # Notificaciones
    reminder_hours_before: int = 12
    notify_closed_day: bool = True
    event_bus: Literal["celery", "sync"] = "celery"
    email_backend: Literal["smtp", "console"] = "smtp"
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = False
    email_from: str = "Pilates Studio <no-reply@pilates.local>"

    frontend_url: str = "http://localhost:3000"
    password_reset_ttl_minutes: int = 60
    invite_ttl_hours: int = 72

    # Admin inicial (opcional, se crea al levantar con `python -m pilates.cli create-admin`)
    admin_email: str | None = None
    admin_password: str | None = None
    admin_name: str = "Administrador"

    cors_origins: str = "*"

    # Rate limiting de login y registro (contadores compartidos en Redis entre workers)
    rate_limit_enabled: bool = True
    rate_limit_storage: Literal["redis", "memory"] = "redis"
    login_attempts_per_ip_per_minute: int = 20
    login_failures_per_email: int = 5
    login_failure_window_minutes: int = 15
    registrations_per_ip_per_hour: int = 10

    @field_validator("morning_slots", "afternoon_slots")
    @classmethod
    def _validate_slots(cls, v: str) -> str:
        _parse_times(v)
        return v

    @property
    def morning_times(self) -> list[time]:
        return _parse_times(self.morning_slots)

    @property
    def afternoon_times(self) -> list[time]:
        return _parse_times(self.afternoon_slots)

    @property
    def reset_url_template(self) -> str:
        return self.frontend_url.rstrip("/") + "/reset-password?token={token}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
