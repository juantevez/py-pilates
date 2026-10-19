from dataclasses import dataclass
from uuid import UUID

from pilates.shared.domain.events import DomainEvent


@dataclass(frozen=True, kw_only=True)
class UserRegistered(DomainEvent):
    user_id: UUID


@dataclass(frozen=True, kw_only=True)
class UserBlocked(DomainEvent):
    user_id: UUID
    reason: str | None


@dataclass(frozen=True, kw_only=True)
class PasswordResetRequested(DomainEvent):
    user_id: UUID
    reset_url: str


@dataclass(frozen=True, kw_only=True)
class UserInvited(DomainEvent):
    """Alta hecha por el gimnasio: el alumno define su contraseña desde el enlace."""

    user_id: UUID
    set_password_url: str
