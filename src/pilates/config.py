from datetime import time
from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_times(value: str | list) -> list[time]:
    if isinstance(value, str):
        value = [v for v in (p.strip() for p in value.split(",")) if v]
    return [v if isinstance(v, time) else time.fromisoformat(v) for v in value]


# HS256 pide una clave de al menos 256 bits; los valores de ejemplo son públicos (están en el repo).
MIN_JWT_SECRET_BYTES = 32
INSECURE_JWT_SECRETS = frozenset({"change-me", "cambiar-por-un-secreto-largo-y-aleatorio"})


class Settings(BaseSettings):
    # hide_input_in_errors: un error de validación no tiene que imprimir secretos en los logs.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    app_name: str = "Pilates Studio API"
    environment: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"

    database_url: str = "postgresql+psycopg://pilates:pilates@localhost:5432/pilates"
    redis_url: str = "redis://localhost:6379/0"
    # Pool por proceso: con WEB_CONCURRENCY workers de uvicorn se abren hasta
    # workers × (pool_size + max_overflow) conexiones (Postgres admite 100 por defecto).
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # Obligatorio: sin un secreto propio cualquiera podría firmar tokens (ver _validate_jwt_secret).
    jwt_secret: str = ""
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
    # Recordatorio: N horas antes de la clase, nunca antes de reminder_earliest_time del día de la clase.
    reminder_hours_before: int = 2
    reminder_earliest_time: time = time(7, 0)
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
    # Swagger (/docs), ReDoc (/redoc) y /openapi.json. En producción se apagan (docker-compose.prod.yml):
    # describen toda la API, incluidos los endpoints de admin.
    api_docs_enabled: bool = True

    # Rate limiting de login, registro y recuperación de contraseña (contadores en Redis compartidos entre workers)
    rate_limit_enabled: bool = True
    rate_limit_storage: Literal["redis", "memory"] = "redis"
    login_attempts_per_ip_per_minute: int = 20
    login_failures_per_email: int = 5
    login_failure_window_minutes: int = 15
    registrations_per_ip_per_hour: int = 10
    password_resets_per_ip_per_hour: int = 5
    password_resets_per_email_per_hour: int = 3

    @field_validator("morning_slots", "afternoon_slots")
    @classmethod
    def _validate_slots(cls, v: str) -> str:
        _parse_times(v)
        return v

    @field_validator("jwt_secret")
    @classmethod
    def _validate_jwt_secret(cls, v: str) -> str:
        if v in INSECURE_JWT_SECRETS:
            raise ValueError("JWT_SECRET no puede ser el valor de ejemplo: generar uno con `openssl rand -hex 32`")
        if len(v.encode()) < MIN_JWT_SECRET_BYTES:
            raise ValueError(f"JWT_SECRET tiene que tener al menos {MIN_JWT_SECRET_BYTES} bytes (`openssl rand -hex 32`)")
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
